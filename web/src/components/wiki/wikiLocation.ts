export type WikiCollectionKind = 'topics' | 'lotcd' | 'team' | 'week'

export interface WikiLocationState {
  collectionPath: string
  topicId: string | null
  kind: WikiCollectionKind
  view: 'docs' | 'graph'
}

function collectionKind(pathname: string): WikiCollectionKind {
  if (pathname.startsWith('/wiki/lotcd')) return 'lotcd'
  if (pathname.startsWith('/wiki/teams')) return 'team'
  if (pathname.startsWith('/wiki/weeks')) return 'week'
  return 'topics'
}

export function parseWikiLocation(pathname: string, search: string): WikiLocationState {
  const params = new URLSearchParams(search)
  const topicMatch = pathname.match(/^\/wiki\/topics\/([^/]+)$/)
  const from = params.get('from')
  const view = params.get('view') === 'graph' ? 'graph' : 'docs'
  params.delete('view')
  const currentCollection = `${pathname}${params.size ? `?${params}` : ''}`
  const collectionPath = topicMatch && from?.startsWith('/wiki/') && !from.startsWith('/wiki/reviews')
    ? from
    : topicMatch ? '/wiki/topics' : currentCollection
  const collectionUrl = new URL(collectionPath, 'http://wiki.local')

  return {
    collectionPath,
    topicId: topicMatch ? decodeURIComponent(topicMatch[1]) : null,
    kind: collectionKind(collectionUrl.pathname),
    view,
  }
}
