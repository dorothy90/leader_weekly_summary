import { render, screen } from '@testing-library/react'

import { EvidenceText } from './EvidenceText'

describe('EvidenceText', () => {
  it('highlights the exact source quote', () => {
    render(
      <EvidenceText
        body="4SA 수율이 하락했습니다. 원인 분석 중입니다."
        quote="4SA 수율이 하락했습니다."
      />,
    )

    expect(screen.getByText('4SA 수율이 하락했습니다.').tagName).toBe('MARK')
  })

  it('keeps the full body when the quote is absent', () => {
    render(<EvidenceText body="전체 메일 본문" quote="없는 근거" />)

    expect(screen.getByText('전체 메일 본문')).toBeInTheDocument()
    expect(document.querySelector('mark')).not.toBeInTheDocument()
  })
})
