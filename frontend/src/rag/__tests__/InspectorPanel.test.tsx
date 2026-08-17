import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ApiExchange, ChatResponse } from '../types'
import { InspectorPanel } from '../InspectorPanel'

const selectedExchange: ApiExchange<ChatResponse> = {
  request: {
    method: 'POST',
    path: '/v1/chat',
    body: { user_id: 'kim', message: '이번 주 일정' },
  },
  response: {
    conversation_id: 'c1',
    answer: '일정 답변 [S1]',
    references: [],
    quality: {
      citation_valid: true,
      limited_answer: false,
      retrieval_mode: 'hybrid',
    },
    disclosures: [],
    trace_id: 'trace-1',
    agent_trace: {
      tool_calls: ['search_calendar'],
      judge_decisions: ['sufficient'],
      iteration_count: 1,
    },
    execution: {
      status: 'succeeded',
      failure_stage: null,
      error_code: null,
      retryable: false,
      search_count: 1,
      evidence_count: 1,
      duration_ms: 12,
      include_in_llm_history: true,
      node_runs: [
        {
          sequence: 1,
          node_name: 'multi_source.tool_executor',
          status: 'ok',
          started_ms: 0,
          duration_ms: 4,
          attempt: 1,
          input: { task_count: 1 },
          output: { evidence_count: 1 },
          error_class: null,
        },
      ],
    },
  },
  status: 200,
  durationMs: 42,
  receivedAt: '2026-08-17T00:00:00Z',
}

describe('InspectorPanel', () => {
  it('shows actual agent execution without route diagnostics', async () => {
    const user = userEvent.setup()
    const copyText = vi.fn().mockResolvedValue(undefined)
    render(
      <InspectorPanel
        exchange={selectedExchange}
        events={[]}
        copyText={copyText}
      />,
    )

    expect(screen.getByText('trace-1')).toBeInTheDocument()
    expect(screen.getByText('search_calendar')).toBeInTheDocument()
    expect(screen.getByText('sufficient')).toBeInTheDocument()
    expect(screen.getByText('succeeded')).toBeInTheDocument()
    expect(screen.getByText('12 ms')).toBeInTheDocument()
    expect(screen.queryByText('Router')).not.toBeInTheDocument()
    expect(screen.queryByText('requested')).not.toBeInTheDocument()
    expect(screen.queryByText('route')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Nodes' }))
    expect(screen.getByText('multi_source.tool_executor')).toBeInTheDocument()
    expect(screen.getByText('+0 ms')).toBeInTheDocument()
    expect(screen.getByText('4 ms')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'JSON' }))
    expect(screen.getByText(/"user_id": "kim"/)).toBeInTheDocument()
    expect(screen.queryByText(/response_mode/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '요청 JSON 복사' }))
    expect(copyText).toHaveBeenCalledWith(
      JSON.stringify(selectedExchange.request, null, 2),
    )
  })
})
