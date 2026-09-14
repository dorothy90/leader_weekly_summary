"""Run only the additive group report application."""
import argparse
from pathlib import Path

import uvicorn


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='그룹 주보 스튜디오')
    parser.add_argument('--port',type=int,default=8091)
    parser.add_argument('--env-file',type=Path,help='명시적으로 지정한 환경 설정 파일만 읽습니다.')
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            parser.error('설정 파일을 찾을 수 없습니다.')
        load_dotenv(args.env_file,override=False)
    # Local, single-process writer. No parent app import or working-directory changes.
    uvicorn.run('studio.api:create_app',factory=True,host='127.0.0.1',port=args.port,workers=1)
