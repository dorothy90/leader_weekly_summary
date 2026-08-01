import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ApiExchange, ChatResponse, RecordedResearchEvent } from '../types'
import { InspectorPanel } from '../InspectorPanel'

const selectedExchange: ApiExchange<ChatResponse> = {
  request: {
    method: 'POST',
    path: '/v1/chat',
    body: { user_id: 'kim', response_mode: 'fast', message: '질문' },
  },
  response: {
    conversation_id: 'c1',
    mode: 'fast_rag',
    answer: '답변',
    references: [],
    quality: {
      citation_valid: true,
      limited_answer: false,
      retrieval_mode: 'hybrid',
    },
    disclosures: [],
    trace_id: 'trace-1',
  },
  status: 200,
  durationMs: 42,
  receivedAt: '2026-08-02T00:00:00Z',
}

const events: RecordedResearchEvent[] = [
  {
    job_id: 'job-1',
    status: 'running',
    progress: 40,
    receivedAt: '2026-08-02T00:00:01Z',
  },
]

describe('InspectorPanel', () => {
  it('switches between summary, safe JSON, and event diagnostics', async () => {
    const user = userEvent.setup()
    const copyText = vi.fn().mockResolvedValue(undefined)
    render(
      <InspectorPanel
        exchange={selectedExchange}
        events={events}
        copyText={copyText}
      />,
    )

    expect(screen.getByText('42 ms')).toBeInTheDocument()
    expect(screen.getByText('trace-1')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'JSON' }))
    expect(screen.getByText(/"user_id": "kim"/)).toBeInTheDocument()
    expect(screen.queryByText(/authorization/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/cookie/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '요청 JSON 복사' }))
    expect(copyText).toHaveBeenCalledWith(
      JSON.stringify(selectedExchange.request, null, 2),
    )

    await user.click(screen.getByRole('tab', { name: 'JSON' }))
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Events' })).toHaveFocus()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('40%')).toBeInTheDocument()
  })
})
