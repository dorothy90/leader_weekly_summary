"""SQLite canonical store for Knowledge Explorer review data."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from knowledge_models import (
    Agenda,
    AgendaDocument,
    AgendaView,
    AliasRecord,
    CategoryPath,
    CandidateMatch,
    ClassificationDecision,
    ClassificationItem,
    ClassificationRun,
    ClassificationRevision,
    Domain,
    GroupAlias,
    Lotcd,
    Mail,
    MailDocument,
    MappingRevision,
    TaxonomyDocument,
    Tech,
    WeekClassificationSummary,
)

if TYPE_CHECKING:
    from agenda_extract import MailExtractionResult


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "knowledge" / "knowledge.db"
FIXTURE_DIR = REPO_ROOT / "fixtures" / "knowledge"


class SQLiteKnowledgeStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS category (
                    id TEXT PRIMARY KEY,
                    level TEXT NOT NULL CHECK(level IN ('domain', 'tech', 'lotcd')),
                    name TEXT NOT NULL,
                    parent_id TEXT REFERENCES category(id),
                    sort_order INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lotcd_metadata (
                    category_id TEXT PRIMARY KEY REFERENCES category(id),
                    fab_id TEXT NOT NULL,
                    product_code TEXT NOT NULL,
                    product TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alias (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    value TEXT NOT NULL,
                    normalized_value TEXT NOT NULL UNIQUE,
                    is_active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS alias_target (
                    alias_id INTEGER NOT NULL REFERENCES alias(id) ON DELETE CASCADE,
                    category_id TEXT NOT NULL REFERENCES category(id) ON DELETE CASCADE,
                    PRIMARY KEY (alias_id, category_id)
                );

                CREATE TABLE IF NOT EXISTS mail (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    sender_team TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    body TEXT NOT NULL,
                    reply_to TEXT REFERENCES mail(id)
                );

                CREATE TABLE IF NOT EXISTS agenda (
                    id TEXT PRIMARY KEY,
                    mail_id TEXT NOT NULL REFERENCES mail(id),
                    source_quote TEXT NOT NULL,
                    source_start INTEGER NOT NULL DEFAULT 0,
                    source_end INTEGER NOT NULL DEFAULT 0,
                    classification_context TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    candidate_paths_json TEXT NOT NULL DEFAULT '[]',
                    topic TEXT NOT NULL,
                    state TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    review_required INTEGER NOT NULL,
                    review_status TEXT NOT NULL CHECK(review_status IN ('pending', 'confirmed', 'on_hold'))
                );

                CREATE TABLE IF NOT EXISTS agenda_target (
                    agenda_id TEXT NOT NULL REFERENCES agenda(id) ON DELETE CASCADE,
                    category_id TEXT NOT NULL REFERENCES category(id),
                    PRIMARY KEY (agenda_id, category_id)
                );

                CREATE TABLE IF NOT EXISTS classification_revision (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agenda_id TEXT NOT NULL REFERENCES agenda(id),
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    changed_by TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mapping_revision (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alias_id INTEGER NOT NULL REFERENCES alias(id),
                    before_json TEXT,
                    after_json TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    changed_by TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_sync_queue (
                    mail_id TEXT PRIMARY KEY REFERENCES mail(id) ON DELETE CASCADE,
                    requested_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS classification_run (
                    id TEXT PRIMARY KEY,
                    week TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('processing', 'completed', 'failed')),
                    prompt_version TEXT NOT NULL,
                    classifier_version TEXT NOT NULL,
                    taxonomy_version INTEGER NOT NULL,
                    alias_version INTEGER NOT NULL,
                    prior_run_id TEXT REFERENCES classification_run(id),
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS week_classification (
                    week TEXT PRIMARY KEY,
                    active_run_id TEXT REFERENCES classification_run(id),
                    workflow_state TEXT NOT NULL,
                    approved_at TEXT,
                    approved_by TEXT
                );

                CREATE TABLE IF NOT EXISTS classification_trace (
                    agenda_id TEXT PRIMARY KEY REFERENCES agenda(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL REFERENCES classification_run(id),
                    item_kind TEXT NOT NULL,
                    decision_status TEXT NOT NULL,
                    target_path_json TEXT,
                    matches_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    taxonomy_version INTEGER NOT NULL,
                    alias_version INTEGER NOT NULL
                );
                """
            )
            agenda_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(agenda)").fetchall()
            }
            for column, definition in (
                ("source_start", "INTEGER NOT NULL DEFAULT 0"),
                ("source_end", "INTEGER NOT NULL DEFAULT 0"),
                ("classification_context", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in agenda_columns:
                    connection.execute(
                        f"ALTER TABLE agenda ADD COLUMN {column} {definition}"
                    )
            alias_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(alias)").fetchall()
            }
            if "is_active" not in alias_columns:
                connection.execute(
                    "ALTER TABLE alias ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
                )
            count = connection.execute("SELECT COUNT(*) FROM category").fetchone()[0]
            if count == 0:
                self._seed(connection)
            elif connection.execute(
                "SELECT COUNT(*) FROM knowledge_meta"
            ).fetchone()[0] == 0:
                fixture = self._fixture("taxonomy.json", TaxonomyDocument)
                self._save_taxonomy_meta(connection, fixture)
            connection.executemany(
                "INSERT OR IGNORE INTO knowledge_meta(key, value) VALUES (?, ?)",
                [
                    ("alias_version", "1"),
                    ("classifier_schema_version", "1"),
                ],
            )

    @staticmethod
    def _fixture(filename: str, model_type):
        path = FIXTURE_DIR / filename
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _category_id(path: CategoryPath, lookups: dict[str, dict]) -> str:
        if path.lotcd:
            lotcd_match = lookups["lotcd"].get(path.lotcd)
            category_id = (
                lotcd_match[0]
                if lotcd_match
                and lotcd_match[1] == path.domain
                and lotcd_match[2] == path.tech
                else None
            )
        elif path.tech:
            category_id = lookups["tech"].get((path.domain, path.tech))
        else:
            category_id = lookups["domain"].get(path.domain)
        if category_id is None:
            raise ValueError(
                f"Unknown category path: {path.domain}/{path.tech}/{path.lotcd}"
            )
        return category_id

    @staticmethod
    def _insert_alias(
        connection: sqlite3.Connection, value: str, target_ids: list[str]
    ) -> None:
        normalized = value.strip().casefold()
        connection.execute(
            "INSERT OR IGNORE INTO alias(value, normalized_value, is_active) VALUES (?, ?, 1)",
            (value.strip(), normalized),
        )
        alias_id = connection.execute(
            "SELECT id FROM alias WHERE normalized_value = ?", (normalized,)
        ).fetchone()[0]
        connection.executemany(
            "INSERT OR IGNORE INTO alias_target(alias_id, category_id) VALUES (?, ?)",
            [(alias_id, target_id) for target_id in target_ids],
        )

    def _seed(self, connection: sqlite3.Connection) -> None:
        taxonomy_path = os.getenv("KNOWLEDGE_TAXONOMY_PATH")
        taxonomy = (
            TaxonomyDocument.model_validate_json(
                Path(taxonomy_path).read_text(encoding="utf-8")
            )
            if taxonomy_path
            else self._fixture("taxonomy.json", TaxonomyDocument)
        )
        mail_document = self._fixture("mails.json", MailDocument)
        agenda_document = self._fixture("expected_agendas.json", AgendaDocument)
        lookups: dict[str, dict] = {"domain": {}, "tech": {}, "lotcd": {}}
        self._save_taxonomy_meta(connection, taxonomy)

        for domain_order, domain in enumerate(taxonomy.domains):
            domain_id = f"domain:{domain.id}"
            lookups["domain"][domain.name] = domain_id
            connection.execute(
                "INSERT INTO category VALUES (?, 'domain', ?, NULL, ?)",
                (domain_id, domain.name, domain_order),
            )
            for tech_order, tech in enumerate(domain.techs):
                tech_id = f"tech:{tech.id}"
                lookups["tech"][(domain.name, tech.name)] = tech_id
                connection.execute(
                    "INSERT INTO category VALUES (?, 'tech', ?, ?, ?)",
                    (tech_id, tech.name, domain_id, tech_order),
                )
                for alias in tech.aliases:
                    self._insert_alias(connection, alias, [tech_id])
                for lotcd_order, lotcd in enumerate(tech.lotcds):
                    lotcd_id = f"lotcd:{lotcd.code}"
                    lookups["lotcd"][lotcd.code] = (
                        lotcd_id,
                        domain.name,
                        tech.name,
                    )
                    connection.execute(
                        "INSERT INTO category VALUES (?, 'lotcd', ?, ?, ?)",
                        (lotcd_id, lotcd.code, tech_id, lotcd_order),
                    )
                    connection.execute(
                        "INSERT INTO lotcd_metadata VALUES (?, ?, ?, ?)",
                        (
                            lotcd_id,
                            lotcd.fab_id,
                            lotcd.product_code,
                            lotcd.product,
                        ),
                    )
                    for alias in lotcd.aliases:
                        self._insert_alias(connection, alias, [lotcd_id])

        for group_alias in taxonomy.group_aliases:
            self._insert_alias(
                connection,
                group_alias.alias,
                [lookups["lotcd"][code][0] for code in group_alias.target_lotcds],
            )

        if taxonomy_path:
            return

        mails_by_id = {mail.id: mail for mail in mail_document.mails}
        for mail in mail_document.mails:
            connection.execute(
                """
                INSERT INTO mail(id, subject, sender_team, sender, received_at, body, reply_to)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mail.id,
                    mail.subject,
                    mail.sender_team,
                    mail.sender,
                    mail.received_at.isoformat(),
                    mail.body,
                    mail.reply_to,
                ),
            )

        for agenda in agenda_document.agendas:
            review_status = "pending" if agenda.review_required else "confirmed"
            source_start = mails_by_id[agenda.mail_id].body.find(agenda.source_quote)
            connection.execute(
                """
                INSERT INTO agenda(
                    id, mail_id, source_quote, source_start, source_end,
                    classification_context, summary, scope,
                    candidate_paths_json, topic, state, confidence,
                    review_required, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agenda.id,
                    agenda.mail_id,
                    agenda.source_quote,
                    source_start,
                    source_start + len(agenda.source_quote),
                    agenda.source_quote,
                    agenda.summary,
                    agenda.scope,
                    json.dumps(
                        [path.model_dump() for path in agenda.candidate_paths],
                        ensure_ascii=False,
                    ),
                    agenda.topic,
                    agenda.state,
                    agenda.confidence,
                    int(agenda.review_required),
                    review_status,
                ),
            )
            connection.executemany(
                "INSERT INTO agenda_target(agenda_id, category_id) VALUES (?, ?)",
                [
                    (agenda.id, self._category_id(path, lookups))
                    for path in agenda.target_paths
                ],
            )

    @staticmethod
    def _save_taxonomy_meta(
        connection: sqlite3.Connection, taxonomy: TaxonomyDocument
    ) -> None:
        connection.executemany(
            "INSERT OR REPLACE INTO knowledge_meta(key, value) VALUES (?, ?)",
            [
                ("version", str(taxonomy.version)),
                ("is_dummy", "true" if taxonomy.is_dummy else "false"),
                ("notice", taxonomy.notice),
            ],
        )

    @staticmethod
    def _single_target_aliases(
        connection: sqlite3.Connection, category_id: str
    ) -> list[str]:
        rows = connection.execute(
            """
            SELECT a.value
            FROM alias a
            JOIN alias_target selected ON selected.alias_id = a.id
            WHERE selected.category_id = ?
              AND a.is_active = 1
              AND (SELECT COUNT(*) FROM alias_target all_targets WHERE all_targets.alias_id = a.id) = 1
            ORDER BY a.id
            """,
            (category_id,),
        ).fetchall()
        return [row["value"] for row in rows]

    @property
    def taxonomy(self) -> TaxonomyDocument:
        with self._connect() as connection:
            taxonomy_meta = {
                row["key"]: row["value"]
                for row in connection.execute("SELECT * FROM knowledge_meta")
            }
            category_rows = connection.execute(
                "SELECT * FROM category ORDER BY sort_order, id"
            ).fetchall()
            by_parent: dict[str | None, list[sqlite3.Row]] = {}
            for row in category_rows:
                by_parent.setdefault(row["parent_id"], []).append(row)

            domains: list[Domain] = []
            for domain_row in by_parent.get(None, []):
                techs: list[Tech] = []
                for tech_row in by_parent.get(domain_row["id"], []):
                    lotcds: list[Lotcd] = []
                    for lotcd_row in by_parent.get(tech_row["id"], []):
                        lotcd_metadata = connection.execute(
                            "SELECT * FROM lotcd_metadata WHERE category_id = ?",
                            (lotcd_row["id"],),
                        ).fetchone()
                        lotcds.append(
                            Lotcd(
                                code=lotcd_row["name"],
                                fab_id=lotcd_metadata["fab_id"],
                                product_code=lotcd_metadata["product_code"],
                                product=lotcd_metadata["product"],
                                aliases=self._single_target_aliases(
                                    connection, lotcd_row["id"]
                                ),
                            )
                        )
                    techs.append(
                        Tech(
                            id=tech_row["id"].split(":", 1)[1],
                            name=tech_row["name"],
                            aliases=self._single_target_aliases(
                                connection, tech_row["id"]
                            ),
                            lotcds=lotcds,
                        )
                    )
                domains.append(
                    Domain(
                        id=domain_row["id"].split(":", 1)[1],
                        name=domain_row["name"],
                        techs=techs,
                    )
                )

            group_rows = connection.execute(
                """
                SELECT a.id, a.value
                FROM alias a
                WHERE a.is_active = 1
                  AND (SELECT COUNT(*) FROM alias_target target WHERE target.alias_id = a.id) > 1
                ORDER BY a.id
                """
            ).fetchall()
            group_aliases = []
            for row in group_rows:
                targets = connection.execute(
                    """
                    SELECT c.name
                    FROM alias_target target
                    JOIN category c ON c.id = target.category_id
                    WHERE target.alias_id = ? AND c.level = 'lotcd'
                    ORDER BY c.sort_order, c.name
                    """,
                    (row["id"],),
                ).fetchall()
                group_aliases.append(
                    GroupAlias(
                        alias=row["value"],
                        target_lotcds=[target["name"] for target in targets],
                    )
                )

        return TaxonomyDocument(
            version=int(taxonomy_meta["version"]),
            is_dummy=taxonomy_meta["is_dummy"] == "true",
            notice=taxonomy_meta["notice"],
            domains=domains,
            group_aliases=group_aliases,
        )

    @property
    def mails(self) -> dict[str, Mail]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM mail").fetchall()
        return {row["id"]: Mail.model_validate(dict(row)) for row in rows}

    def _category_paths(
        self, connection: sqlite3.Connection, agenda_id: str
    ) -> list[CategoryPath]:
        rows = connection.execute(
            """
            SELECT c.id, c.level, c.name, c.parent_id
            FROM agenda_target target
            JOIN category c ON c.id = target.category_id
            WHERE target.agenda_id = ?
            ORDER BY c.id
            """,
            (agenda_id,),
        ).fetchall()
        categories = {
            row["id"]: row
            for row in connection.execute("SELECT * FROM category").fetchall()
        }
        paths: list[CategoryPath] = []
        for row in rows:
            if row["level"] == "domain":
                paths.append(CategoryPath(domain=row["name"], tech=None, lotcd=None))
            elif row["level"] == "tech":
                domain = categories[row["parent_id"]]
                paths.append(
                    CategoryPath(domain=domain["name"], tech=row["name"], lotcd=None)
                )
            else:
                tech = categories[row["parent_id"]]
                domain = categories[tech["parent_id"]]
                paths.append(
                    CategoryPath(
                        domain=domain["name"], tech=tech["name"], lotcd=row["name"]
                    )
                )
        return paths

    def _agenda_from_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> Agenda:
        return Agenda(
            id=row["id"],
            mail_id=row["mail_id"],
            source_quote=row["source_quote"],
            summary=row["summary"],
            scope=row["scope"],
            target_paths=self._category_paths(connection, row["id"]),
            candidate_paths=[
                CategoryPath.model_validate(path)
                for path in json.loads(row["candidate_paths_json"])
            ],
            topic=row["topic"],
            state=row["state"],
            confidence=row["confidence"],
            review_required=bool(row["review_required"]),
        )

    @property
    def agendas(self) -> list[Agenda]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM agenda").fetchall()
            return [self._agenda_from_row(connection, row) for row in rows]

    def agenda_view(self, agenda: Agenda) -> AgendaView:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT m.subject, m.sender_team, m.received_at, a.review_status
                FROM agenda a JOIN mail m ON m.id = a.mail_id
                WHERE a.id = ?
                """,
                (agenda.id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Unknown agenda: {agenda.id}")
        return AgendaView(
            **agenda.model_dump(),
            subject=row["subject"],
            sender_team=row["sender_team"],
            received_at=row["received_at"],
            review_status=row["review_status"],
        )

    def update_classification(
        self,
        agenda_id: str,
        target_paths: list[CategoryPath],
        review_status: str,
        changed_by: str,
    ) -> Agenda:
        taxonomy = self.taxonomy
        lookups: dict[str, dict] = {"domain": {}, "tech": {}, "lotcd": {}}
        for domain in taxonomy.domains:
            lookups["domain"][domain.name] = f"domain:{domain.id}"
            for tech in domain.techs:
                lookups["tech"][(domain.name, tech.name)] = f"tech:{tech.id}"
                for lotcd in tech.lotcds:
                    lookups["lotcd"][lotcd.code] = (
                        f"lotcd:{lotcd.code}",
                        domain.name,
                        tech.name,
                    )
        target_ids = [self._category_id(path, lookups) for path in target_paths]
        domains = {path.domain for path in target_paths}
        if not target_paths:
            scope = "unknown"
        elif len(domains) > 1:
            scope = "cross_domain"
        elif len(target_paths) > 1:
            scope = "multi_lotcd"
        elif target_paths[0].lotcd:
            scope = "lotcd"
        elif target_paths[0].tech:
            scope = "tech"
        else:
            scope = "domain"

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agenda WHERE id = ?", (agenda_id,)
            ).fetchone()
            if row is None:
                raise KeyError(agenda_id)
            before = self._agenda_from_row(connection, row).model_dump(mode="json")
            review_required = review_status != "confirmed"
            connection.execute(
                """
                UPDATE agenda
                SET scope = ?, review_required = ?, review_status = ?, confidence = ?
                WHERE id = ?
                """,
                (
                    scope,
                    int(review_required),
                    review_status,
                    1.0 if review_status == "confirmed" else row["confidence"],
                    agenda_id,
                ),
            )
            connection.execute(
                "DELETE FROM agenda_target WHERE agenda_id = ?", (agenda_id,)
            )
            connection.executemany(
                "INSERT INTO agenda_target(agenda_id, category_id) VALUES (?, ?)",
                [(agenda_id, category_id) for category_id in dict.fromkeys(target_ids)],
            )
            updated_row = connection.execute(
                "SELECT * FROM agenda WHERE id = ?", (agenda_id,)
            ).fetchone()
            after_model = self._agenda_from_row(connection, updated_row)
            after = after_model.model_dump(mode="json")
            connection.execute(
                """
                INSERT INTO classification_revision(
                    agenda_id, before_json, after_json, changed_at, changed_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    agenda_id,
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(after, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    changed_by,
                ),
            )
            connection.execute(
                """
                INSERT INTO search_sync_queue(mail_id, requested_at)
                VALUES (?, ?)
                ON CONFLICT(mail_id) DO UPDATE SET requested_at = excluded.requested_at
                """,
                (row["mail_id"], datetime.now(UTC).isoformat()),
            )
            return after_model

    def revisions(self, agenda_id: str) -> list[ClassificationRevision]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM classification_revision
                WHERE agenda_id = ? ORDER BY id DESC
                """,
                (agenda_id,),
            ).fetchall()
        return [
            ClassificationRevision(
                id=row["id"],
                agenda_id=row["agenda_id"],
                before=json.loads(row["before_json"]),
                after=json.loads(row["after_json"]),
                changed_at=row["changed_at"],
                changed_by=row["changed_by"],
            )
            for row in rows
        ]

    def _path_for_category_id(
        self, connection: sqlite3.Connection, category_id: str
    ) -> CategoryPath:
        categories = {
            row["id"]: row
            for row in connection.execute("SELECT * FROM category").fetchall()
        }
        row = categories.get(category_id)
        if row is None:
            raise ValueError(f"Unknown category ID: {category_id}")
        if row["level"] == "domain":
            return CategoryPath(domain=row["name"], tech=None, lotcd=None)
        if row["level"] == "tech":
            domain = categories[row["parent_id"]]
            return CategoryPath(domain=domain["name"], tech=row["name"], lotcd=None)
        tech = categories[row["parent_id"]]
        domain = categories[tech["parent_id"]]
        return CategoryPath(
            domain=domain["name"], tech=tech["name"], lotcd=row["name"]
        )

    def _alias_record(
        self, connection: sqlite3.Connection, alias_id: int
    ) -> AliasRecord:
        alias_row = connection.execute(
            "SELECT * FROM alias WHERE id = ? AND is_active = 1", (alias_id,)
        ).fetchone()
        if alias_row is None:
            raise KeyError(alias_id)
        target_rows = connection.execute(
            "SELECT category_id FROM alias_target WHERE alias_id = ? ORDER BY category_id",
            (alias_id,),
        ).fetchall()
        return AliasRecord(
            id=alias_row["id"],
            value=alias_row["value"],
            target_paths=[
                self._path_for_category_id(connection, row["category_id"])
                for row in target_rows
            ],
        )

    def aliases(self) -> list[AliasRecord]:
        with self._connect() as connection:
            ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM alias WHERE is_active = 1"
                )
            ]
            return [self._alias_record(connection, alias_id) for alias_id in ids]

    def _lookups(self) -> dict[str, dict]:
        lookups: dict[str, dict] = {"domain": {}, "tech": {}, "lotcd": {}}
        for domain in self.taxonomy.domains:
            lookups["domain"][domain.name] = f"domain:{domain.id}"
            for tech in domain.techs:
                lookups["tech"][(domain.name, tech.name)] = f"tech:{tech.id}"
                for lotcd in tech.lotcds:
                    lookups["lotcd"][lotcd.code] = (
                        f"lotcd:{lotcd.code}",
                        domain.name,
                        tech.name,
                    )
        return lookups

    def create_alias(
        self, value: str, target_paths: list[CategoryPath], changed_by: str
    ) -> AliasRecord:
        normalized = value.strip().casefold()
        lookups = self._lookups()
        target_ids = [
            self._category_id(path, lookups) for path in target_paths
        ]
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM alias WHERE normalized_value = ? AND is_active = 1",
                (normalized,),
            ).fetchone()
            if existing:
                raise ValueError(f"Alias already exists: {value.strip()}")
            cursor = connection.execute(
                "INSERT INTO alias(value, normalized_value, is_active) VALUES (?, ?, 1)",
                (value.strip(), normalized),
            )
            alias_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO alias_target(alias_id, category_id) VALUES (?, ?)",
                [(alias_id, target_id) for target_id in dict.fromkeys(target_ids)],
            )
            record = self._alias_record(connection, alias_id)
            connection.execute(
                """
                INSERT INTO mapping_revision(
                    alias_id, before_json, after_json, changed_at, changed_by
                ) VALUES (?, NULL, ?, ?, ?)
                """,
                (
                    alias_id,
                    record.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                    changed_by,
                ),
            )
            return record

    def update_alias(
        self,
        alias_id: int,
        value: str,
        target_paths: list[CategoryPath],
        changed_by: str,
    ) -> AliasRecord:
        normalized = value.strip().casefold()
        lookups = self._lookups()
        target_ids = [
            self._category_id(path, lookups) for path in target_paths
        ]
        with self._connect() as connection:
            before = self._alias_record(connection, alias_id)
            conflict = connection.execute(
                "SELECT id FROM alias WHERE normalized_value = ? AND id != ? AND is_active = 1",
                (normalized, alias_id),
            ).fetchone()
            if conflict:
                raise ValueError(f"Alias already exists: {value.strip()}")
            connection.execute(
                "UPDATE alias SET value = ?, normalized_value = ? WHERE id = ?",
                (value.strip(), normalized, alias_id),
            )
            connection.execute("DELETE FROM alias_target WHERE alias_id = ?", (alias_id,))
            connection.executemany(
                "INSERT INTO alias_target(alias_id, category_id) VALUES (?, ?)",
                [(alias_id, target_id) for target_id in dict.fromkeys(target_ids)],
            )
            after = self._alias_record(connection, alias_id)
            connection.execute(
                """
                INSERT INTO mapping_revision(
                    alias_id, before_json, after_json, changed_at, changed_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    alias_id,
                    before.model_dump_json(),
                    after.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                    changed_by,
                ),
            )
            return after

    def delete_alias(self, alias_id: int, changed_by: str) -> AliasRecord:
        with self._connect() as connection:
            before = self._alias_record(connection, alias_id)
            after = {**before.model_dump(mode="json"), "deleted": True}
            connection.execute(
                """
                UPDATE alias
                SET is_active = 0, normalized_value = ?
                WHERE id = ?
                """,
                (f"__deleted__:{alias_id}:{before.value.casefold()}", alias_id),
            )
            connection.execute(
                """
                INSERT INTO mapping_revision(
                    alias_id, before_json, after_json, changed_at, changed_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    alias_id,
                    before.model_dump_json(),
                    json.dumps(after, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    changed_by,
                ),
            )
            return before

    def mapping_revisions(self, alias_id: int) -> list[MappingRevision]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM mapping_revision WHERE alias_id = ? ORDER BY id DESC",
                (alias_id,),
            ).fetchall()
        return [
            MappingRevision(
                id=row["id"],
                alias_id=row["alias_id"],
                before=json.loads(row["before_json"]) if row["before_json"] else None,
                after=json.loads(row["after_json"]),
                changed_at=row["changed_at"],
                changed_by=row["changed_by"],
            )
            for row in rows
        ]

    @staticmethod
    def _classification_run_from_row(row: sqlite3.Row) -> ClassificationRun:
        return ClassificationRun.model_validate(dict(row))

    @staticmethod
    def _active_processing_run(
        connection: sqlite3.Connection, run_id: str
    ) -> sqlite3.Row:
        run = connection.execute(
            "SELECT * FROM classification_run WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(run_id)
        active = connection.execute(
            "SELECT active_run_id FROM week_classification WHERE week = ?",
            (run["week"],),
        ).fetchone()
        if (
            run["status"] != "processing"
            or active is None
            or active["active_run_id"] != run_id
        ):
            raise ValueError(f"Classification run is not active processing: {run_id}")
        return run

    def start_classification_run(
        self,
        week: str,
        prompt_version: str,
        classifier_version: str,
    ) -> ClassificationRun:
        run_id = uuid.uuid4().hex
        started_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            metadata = {
                row["key"]: row["value"]
                for row in connection.execute(
                    "SELECT key, value FROM knowledge_meta"
                )
            }
            current = connection.execute(
                "SELECT * FROM week_classification WHERE week = ?",
                (week,),
            ).fetchone()
            if current and current["workflow_state"] in {
                "approved",
                "revalidation_required",
            }:
                raise ValueError(
                    f"Week {week} cannot start a classification run while "
                    f"{current['workflow_state']}"
                )
            if current and current["active_run_id"]:
                active_status = connection.execute(
                    "SELECT status FROM classification_run WHERE id = ?",
                    (current["active_run_id"],),
                ).fetchone()
                if active_status and active_status["status"] == "processing":
                    raise ValueError(
                        f"Week {week} is already processing run "
                        f"{current['active_run_id']}"
                    )
            prior_run_id = current["active_run_id"] if current else None
            connection.execute(
                """
                INSERT INTO classification_run(
                    id, week, status, prompt_version, classifier_version,
                    taxonomy_version, alias_version, prior_run_id, started_at
                ) VALUES (?, ?, 'processing', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    week,
                    prompt_version,
                    classifier_version,
                    int(metadata["version"]),
                    int(metadata["alias_version"]),
                    prior_run_id,
                    started_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO week_classification(
                    week, active_run_id, workflow_state, approved_at, approved_by
                ) VALUES (?, ?, 'processing', NULL, NULL)
                ON CONFLICT(week) DO UPDATE SET
                    active_run_id = excluded.active_run_id,
                    workflow_state = excluded.workflow_state,
                    approved_at = NULL,
                    approved_by = NULL
                """,
                (week, run_id),
            )
            row = connection.execute(
                "SELECT * FROM classification_run WHERE id = ?", (run_id,)
            ).fetchone()
        return self._classification_run_from_row(row)

    def finish_classification_run(self, run_id: str) -> WeekClassificationSummary:
        completed_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            run = self._active_processing_run(connection, run_id)
            trace_counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(
                        decision_status IN (
                            'unclassified', 'conflict', 'review_required'
                        )
                    ), 0) AS unresolved
                FROM classification_trace
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            workflow_state = (
                "review_in_progress"
                if trace_counts["total"] == 0 or trace_counts["unresolved"]
                else "ready_for_approval"
            )
            connection.execute(
                """
                UPDATE classification_run
                SET status = 'completed', completed_at = ?, error = NULL
                WHERE id = ?
                """,
                (completed_at, run_id),
            )
            connection.execute(
                """
                UPDATE week_classification SET workflow_state = ?
                WHERE week = ? AND active_run_id = ?
                """,
                (workflow_state, run["week"], run_id),
            )
        return self.week_summary(run["week"])

    def fail_classification_run(
        self, run_id: str, error: str
    ) -> WeekClassificationSummary:
        completed_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            run = self._active_processing_run(connection, run_id)
            connection.execute(
                """
                UPDATE classification_run
                SET status = 'failed', completed_at = ?, error = ?
                WHERE id = ?
                """,
                (completed_at, error, run_id),
            )
            connection.execute(
                """
                UPDATE week_classification SET workflow_state = 'failed'
                WHERE week = ? AND active_run_id = ?
                """,
                (run["week"], run_id),
            )
        return self.week_summary(run["week"])

    def save_classified_extraction(
        self,
        run_id: str,
        mail: Mail,
        result: "MailExtractionResult",
    ) -> dict[str, int]:
        with self._connect() as connection:
            run = self._active_processing_run(connection, run_id)

            counts = self._save_extraction(connection, mail, result)
            for agenda in result.agendas:
                decision = agenda.decision
                connection.execute(
                    """
                    INSERT INTO classification_trace(
                        agenda_id, run_id, item_kind, decision_status,
                        target_path_json, matches_json, diagnostics_json,
                        prompt_version, taxonomy_version, alias_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agenda_id) DO UPDATE SET
                        run_id = excluded.run_id,
                        item_kind = excluded.item_kind,
                        decision_status = excluded.decision_status,
                        target_path_json = excluded.target_path_json,
                        matches_json = excluded.matches_json,
                        diagnostics_json = excluded.diagnostics_json,
                        prompt_version = excluded.prompt_version,
                        taxonomy_version = excluded.taxonomy_version,
                        alias_version = excluded.alias_version
                    """,
                    (
                        agenda.id,
                        run_id,
                        agenda.item_kind,
                        decision.status,
                        (
                            decision.target_path.model_dump_json()
                            if decision.target_path
                            else None
                        ),
                        json.dumps(
                            [match.model_dump() for match in decision.matches],
                            ensure_ascii=False,
                        ),
                        json.dumps(decision.diagnostics, ensure_ascii=False),
                        run["prompt_version"],
                        run["taxonomy_version"],
                        run["alias_version"],
                    ),
                )
            return counts

    def classification_items(self, week: str) -> list[ClassificationItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    a.id AS agenda_id, a.mail_id, a.summary, a.source_quote,
                    a.classification_context, a.confidence, t.item_kind,
                    t.decision_status, t.target_path_json, t.matches_json,
                    t.diagnostics_json,
                    (SELECT COUNT(*) FROM classification_revision revision
                     WHERE revision.agenda_id = a.id) AS revision_count
                FROM week_classification week
                JOIN classification_trace t ON t.run_id = week.active_run_id
                JOIN agenda a ON a.id = t.agenda_id
                WHERE week.week = ?
                ORDER BY a.id
                """,
                (week,),
            ).fetchall()
        return [
            ClassificationItem(
                agenda_id=row["agenda_id"],
                mail_id=row["mail_id"],
                summary=row["summary"],
                source_quote=row["source_quote"],
                classification_context=row["classification_context"],
                item_kind=row["item_kind"],
                decision=ClassificationDecision(
                    status=row["decision_status"],
                    target_path=(
                        CategoryPath.model_validate_json(row["target_path_json"])
                        if row["target_path_json"]
                        else None
                    ),
                    matches=[
                        CandidateMatch.model_validate(match)
                        for match in json.loads(row["matches_json"])
                    ],
                    diagnostics=json.loads(row["diagnostics_json"]),
                    confidence=row["confidence"],
                ),
                revision_count=row["revision_count"],
            )
            for row in rows
        ]

    def week_summary(self, week: str) -> WeekClassificationSummary:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM week_classification WHERE week = ?", (week,)
            ).fetchone()
            if row is None:
                return WeekClassificationSummary(
                    week=week, workflow_state="not_started"
                )
            count_rows = connection.execute(
                """
                SELECT decision_status, COUNT(*) AS total
                FROM classification_trace
                WHERE run_id = ?
                GROUP BY decision_status
                """,
                (row["active_run_id"],),
            ).fetchall()
        return WeekClassificationSummary(
            week=week,
            workflow_state=row["workflow_state"],
            active_run_id=row["active_run_id"],
            counts={item["decision_status"]: item["total"] for item in count_rows},
        )

    def week_summaries(self) -> list[WeekClassificationSummary]:
        with self._connect() as connection:
            weeks = [
                row["week"]
                for row in connection.execute(
                    "SELECT week FROM week_classification ORDER BY week DESC"
                )
            ]
        return [self.week_summary(week) for week in weeks]

    def approve_week(
        self, week: str, approved_by: str
    ) -> WeekClassificationSummary:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM week_classification WHERE week = ?", (week,)
            ).fetchone()
            if row is None:
                raise KeyError(week)
            trace_counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(
                        decision_status IN (
                            'unclassified', 'conflict', 'review_required'
                        )
                    ), 0) AS unresolved
                FROM classification_trace
                WHERE run_id = ?
                """,
                (row["active_run_id"],),
            ).fetchone()
            if trace_counts["total"] == 0:
                raise ValueError(f"Week {week} has no classification items")
            if trace_counts["unresolved"]:
                raise ValueError(
                    f"Week {week} has {trace_counts['unresolved']} "
                    "unresolved classification items"
                )
            if row["workflow_state"] != "ready_for_approval":
                raise ValueError(f"Week {week} is not ready for approval")
            connection.execute(
                """
                UPDATE week_classification
                SET workflow_state = 'approved', approved_at = ?, approved_by = ?
                WHERE week = ?
                """,
                (datetime.now(UTC).isoformat(), approved_by, week),
            )
        return self.week_summary(week)

    def save_extraction(
        self, mail: Mail, result: "MailExtractionResult"
    ) -> dict[str, int]:
        """Upsert extracted agendas while preserving manually reviewed rows."""
        with self._connect() as connection:
            return self._save_extraction(connection, mail, result)

    def _save_extraction(
        self,
        connection: sqlite3.Connection,
        mail: Mail,
        result: "MailExtractionResult",
    ) -> dict[str, int]:
        lookups = self._lookups()
        counts = {"inserted": 0, "updated": 0, "preserved": 0, "removed": 0}
        current_ids = {agenda.id for agenda in result.agendas}

        connection.execute(
            """
            INSERT INTO mail(id, subject, sender_team, sender, received_at, body, reply_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                subject = excluded.subject,
                sender_team = excluded.sender_team,
                sender = excluded.sender,
                received_at = excluded.received_at,
                body = excluded.body,
                reply_to = excluded.reply_to
            """,
            (
                mail.id,
                mail.subject,
                mail.sender_team,
                mail.sender,
                mail.received_at.isoformat(),
                mail.body,
                mail.reply_to,
            ),
        )

        old_rows = connection.execute(
            "SELECT id FROM agenda WHERE mail_id = ?", (mail.id,)
        ).fetchall()
        for row in old_rows:
            if row["id"] in current_ids:
                continue
            revision_count = connection.execute(
                "SELECT COUNT(*) FROM classification_revision WHERE agenda_id = ?",
                (row["id"],),
            ).fetchone()[0]
            if revision_count:
                counts["preserved"] += 1
            else:
                connection.execute("DELETE FROM agenda WHERE id = ?", (row["id"],))
                counts["removed"] += 1

        for extracted in result.agendas:
            exists = connection.execute(
                "SELECT 1 FROM agenda WHERE id = ?", (extracted.id,)
            ).fetchone()
            revision_count = connection.execute(
                "SELECT COUNT(*) FROM classification_revision WHERE agenda_id = ?",
                (extracted.id,),
            ).fetchone()[0]
            if exists and revision_count:
                counts["preserved"] += 1
                continue

            review_status = "pending" if extracted.review_required else "confirmed"
            connection.execute(
                """
                INSERT INTO agenda(
                    id, mail_id, source_quote, source_start, source_end,
                    classification_context, summary, scope,
                    candidate_paths_json, topic, state, confidence,
                    review_required, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_quote = excluded.source_quote,
                    source_start = excluded.source_start,
                    source_end = excluded.source_end,
                    classification_context = excluded.classification_context,
                    summary = excluded.summary,
                    scope = excluded.scope,
                    candidate_paths_json = excluded.candidate_paths_json,
                    topic = excluded.topic,
                    state = excluded.state,
                    confidence = excluded.confidence,
                    review_required = excluded.review_required,
                    review_status = excluded.review_status
                """,
                (
                    extracted.id,
                    mail.id,
                    extracted.source_quote,
                    extracted.source_start,
                    extracted.source_end,
                    extracted.classification_context,
                    extracted.summary,
                    extracted.scope,
                    json.dumps(
                        [path.model_dump() for path in extracted.candidate_paths],
                        ensure_ascii=False,
                    ),
                    extracted.topic,
                    extracted.state,
                    extracted.confidence,
                    int(extracted.review_required),
                    review_status,
                ),
            )
            connection.execute(
                "DELETE FROM agenda_target WHERE agenda_id = ?", (extracted.id,)
            )
            connection.executemany(
                "INSERT INTO agenda_target(agenda_id, category_id) VALUES (?, ?)",
                [
                    (extracted.id, self._category_id(path, lookups))
                    for path in extracted.target_paths
                ],
            )
            counts["updated" if exists else "inserted"] += 1

        connection.execute(
            """
            INSERT INTO search_sync_queue(mail_id, requested_at)
            VALUES (?, ?)
            ON CONFLICT(mail_id) DO UPDATE SET requested_at = excluded.requested_at
            """,
            (mail.id, datetime.now(UTC).isoformat()),
        )

        return counts

    def pending_search_sync(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT mail_id FROM search_sync_queue ORDER BY requested_at"
            ).fetchall()
        return [row["mail_id"] for row in rows]

    def complete_search_sync(self, mail_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM search_sync_queue WHERE mail_id = ?", (mail_id,)
            )

    @staticmethod
    def _next_sort_order(
        connection: sqlite3.Connection, parent_id: str
    ) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM category WHERE parent_id = ?",
            (parent_id,),
        ).fetchone()
        return int(row["next_order"])

    def create_tech(
        self,
        domain: str,
        tech_id: str,
        name: str,
        aliases: list[str],
    ) -> TaxonomyDocument:
        domain_id = f"domain:{domain.lower()}"
        category_id = f"tech:{tech_id}"
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM category WHERE id = ?", (domain_id,)
            ).fetchone() is None:
                raise ValueError(f"Unknown domain: {domain}")
            if connection.execute(
                "SELECT 1 FROM category WHERE id = ? OR (parent_id = ? AND name = ?)",
                (category_id, domain_id, name),
            ).fetchone():
                raise ValueError(f"Tech already exists: {name}")
            connection.execute(
                "INSERT INTO category VALUES (?, 'tech', ?, ?, ?)",
                (
                    category_id,
                    name.strip(),
                    domain_id,
                    self._next_sort_order(connection, domain_id),
                ),
            )
            for alias in aliases:
                self._insert_alias(connection, alias, [category_id])
        return self.taxonomy

    def update_tech(
        self,
        tech_id: str,
        name: str,
        aliases: list[str],
    ) -> TaxonomyDocument:
        category_id = f"tech:{tech_id}"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT parent_id FROM category WHERE id = ? AND level = 'tech'",
                (category_id,),
            ).fetchone()
            if row is None:
                raise KeyError(tech_id)
            if connection.execute(
                "SELECT 1 FROM category WHERE parent_id = ? AND name = ? AND id != ?",
                (row["parent_id"], name.strip(), category_id),
            ).fetchone():
                raise ValueError(f"Tech already exists: {name.strip()}")
            connection.execute(
                "UPDATE category SET name = ? WHERE id = ?",
                (name.strip(), category_id),
            )
            single_alias_ids = [
                alias_row["id"]
                for alias_row in connection.execute(
                    """
                    SELECT a.id
                    FROM alias a
                    JOIN alias_target target ON target.alias_id = a.id
                    WHERE target.category_id = ?
                      AND a.is_active = 1
                      AND (SELECT COUNT(*) FROM alias_target all_targets WHERE all_targets.alias_id = a.id) = 1
                    """,
                    (category_id,),
                ).fetchall()
            ]
            for alias_id in single_alias_ids:
                alias_row = connection.execute(
                    "SELECT value FROM alias WHERE id = ?", (alias_id,)
                ).fetchone()
                connection.execute(
                    "UPDATE alias SET is_active = 0, normalized_value = ? WHERE id = ?",
                    (f"__deleted__:{alias_id}:{alias_row['value'].casefold()}", alias_id),
                )
            for alias in aliases:
                self._insert_alias(connection, alias, [category_id])
        return self.taxonomy

    def delete_tech(self, tech_id: str) -> TaxonomyDocument:
        category_id = f"tech:{tech_id}"
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM category WHERE id = ? AND level = 'tech'",
                (category_id,),
            ).fetchone() is None:
                raise KeyError(tech_id)
            child_count = connection.execute(
                "SELECT COUNT(*) FROM category WHERE parent_id = ?", (category_id,)
            ).fetchone()[0]
            if child_count:
                raise ValueError(
                    f"Tech has {child_count} LOTCD child category(s): {tech_id}"
                )
            linked = connection.execute(
                "SELECT COUNT(*) FROM agenda_target WHERE category_id = ?",
                (category_id,),
            ).fetchone()[0]
            if linked:
                raise ValueError(f"Tech is linked to {linked} agenda(s): {tech_id}")
            alias_ids = [
                row["alias_id"]
                for row in connection.execute(
                    "SELECT alias_id FROM alias_target WHERE category_id = ?",
                    (category_id,),
                ).fetchall()
            ]
            connection.execute("DELETE FROM category WHERE id = ?", (category_id,))
            for alias_id in alias_ids:
                remaining = connection.execute(
                    "SELECT COUNT(*) FROM alias_target WHERE alias_id = ?", (alias_id,)
                ).fetchone()[0]
                if remaining == 0:
                    alias_row = connection.execute(
                        "SELECT value FROM alias WHERE id = ?", (alias_id,)
                    ).fetchone()
                    connection.execute(
                        "UPDATE alias SET is_active = 0, normalized_value = ? WHERE id = ?",
                        (f"__deleted__:{alias_id}:{alias_row['value'].casefold()}", alias_id),
                    )
        return self.taxonomy

    def create_lotcd(
        self,
        domain: str,
        tech: str,
        code: str,
        fab_id: str,
        product_code: str,
        product: str,
        aliases: list[str],
    ) -> TaxonomyDocument:
        taxonomy = self.taxonomy
        selected_domain = next(
            (item for item in taxonomy.domains if item.name == domain), None
        )
        selected_tech = (
            next((item for item in selected_domain.techs if item.name == tech), None)
            if selected_domain
            else None
        )
        if selected_tech is None:
            raise ValueError(f"Unknown Tech: {domain}/{tech}")
        category_id = f"lotcd:{code}"
        tech_category_id = f"tech:{selected_tech.id}"
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM category WHERE id = ?", (category_id,)
            ).fetchone():
                raise ValueError(f"LOTCD already exists: {code}")
            connection.execute(
                "INSERT INTO category VALUES (?, 'lotcd', ?, ?, ?)",
                (
                    category_id,
                    code,
                    tech_category_id,
                    self._next_sort_order(connection, tech_category_id),
                ),
            )
            connection.execute(
                "INSERT INTO lotcd_metadata VALUES (?, ?, ?, ?)",
                (category_id, fab_id.strip(), product_code.strip(), product.strip()),
            )
            for alias in aliases:
                self._insert_alias(connection, alias, [category_id])
        return self.taxonomy

    def update_lotcd(
        self,
        code: str,
        fab_id: str,
        product_code: str,
        product: str,
        aliases: list[str],
    ) -> TaxonomyDocument:
        category_id = f"lotcd:{code}"
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM category WHERE id = ? AND level = 'lotcd'",
                (category_id,),
            ).fetchone() is None:
                raise KeyError(code)
            connection.execute(
                """
                UPDATE lotcd_metadata
                SET fab_id = ?, product_code = ?, product = ?
                WHERE category_id = ?
                """,
                (fab_id.strip(), product_code.strip(), product.strip(), category_id),
            )
            single_alias_ids = [
                row["id"]
                for row in connection.execute(
                    """
                    SELECT a.id
                    FROM alias a
                    JOIN alias_target target ON target.alias_id = a.id
                    WHERE target.category_id = ?
                      AND a.is_active = 1
                      AND (SELECT COUNT(*) FROM alias_target all_targets WHERE all_targets.alias_id = a.id) = 1
                    """,
                    (category_id,),
                ).fetchall()
            ]
            for alias_id in single_alias_ids:
                alias_row = connection.execute(
                    "SELECT value FROM alias WHERE id = ?", (alias_id,)
                ).fetchone()
                connection.execute(
                    """
                    UPDATE alias
                    SET is_active = 0, normalized_value = ?
                    WHERE id = ?
                    """,
                    (
                        f"__deleted__:{alias_id}:{alias_row['value'].casefold()}",
                        alias_id,
                    ),
                )
            for alias in aliases:
                self._insert_alias(connection, alias, [category_id])
        return self.taxonomy

    def delete_lotcd(self, code: str) -> TaxonomyDocument:
        category_id = f"lotcd:{code}"
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM category WHERE id = ? AND level = 'lotcd'",
                (category_id,),
            ).fetchone() is None:
                raise KeyError(code)
            linked = connection.execute(
                "SELECT COUNT(*) FROM agenda_target WHERE category_id = ?",
                (category_id,),
            ).fetchone()[0]
            if linked:
                raise ValueError(f"LOTCD is linked to {linked} agenda(s): {code}")
            alias_ids = [
                row["alias_id"]
                for row in connection.execute(
                    "SELECT alias_id FROM alias_target WHERE category_id = ?",
                    (category_id,),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM lotcd_metadata WHERE category_id = ?", (category_id,)
            )
            connection.execute("DELETE FROM category WHERE id = ?", (category_id,))
            for alias_id in alias_ids:
                remaining = connection.execute(
                    "SELECT COUNT(*) FROM alias_target WHERE alias_id = ?", (alias_id,)
                ).fetchone()[0]
                if remaining == 0:
                    alias_row = connection.execute(
                        "SELECT value FROM alias WHERE id = ?", (alias_id,)
                    ).fetchone()
                    connection.execute(
                        """
                        UPDATE alias
                        SET is_active = 0, normalized_value = ?
                        WHERE id = ?
                        """,
                        (
                            f"__deleted__:{alias_id}:{alias_row['value'].casefold()}",
                            alias_id,
                        ),
                    )
        return self.taxonomy
