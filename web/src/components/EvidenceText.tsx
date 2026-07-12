interface EvidenceTextProps {
  body: string
  quote: string
}

export function EvidenceText({ body, quote }: EvidenceTextProps) {
  const index = body.indexOf(quote)
  if (index < 0) {
    return <p className="mail-body">{body}</p>
  }

  return (
    <p className="mail-body">
      {body.slice(0, index)}
      <mark>{quote}</mark>
      {body.slice(index + quote.length)}
    </p>
  )
}
