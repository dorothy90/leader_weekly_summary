import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { RagApiClient } from '../../services/ragApiService'
import type {
  ApiExchange,
  ChatResponse,
  HealthResponse,
  ReadinessResponse,
} from '../types'
import { RagLabApp } from '../RagLabApp'

const exchange = <T,>(response: T, status = 200): ApiExchange<T> => ({
  request: { method: 'POST', path: '/v1/chat', body: { user_id: 'kim' } },
  response,
  status,
  durationMs: 184,
  receivedAt: '2026-08-17T00:00:00Z',
})

const health = exchange<HealthResponse>({ status: 'ok' })
const readiness = exchange<ReadinessResponse>({
  status: 'ready',
  dependencies: { mongo: 'ready', opensearch: 'ready' },
})

const chatResponse = (updates: Partial<ChatResponse> = {}): ChatResponse => ({
  conversation_id: 'conversation-1',
  answer: '확인된 답변입니다 [S1]',
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
    duration_ms: 20,
    include_in_llm_history: true,
    node_runs: [],
  },
  ...updates,
})

const baseService = (): RagApiClient => ({
  sendChat: vi.fn(),
  researchAction: vi.fn(),
  streamResearchEvents: vi.fn(),
  getHealth: vi.fn().mockResolvedValue(health),
  getReadiness: vi.fn().mockResolvedValue(readiness),
})

async function submitQuestion(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('user_id'), 'kim')
  await user.type(screen.getByRole('textbox', { name: '메시지' }), '최근 현황을 알려줘')
  await user.click(screen.getByRole('button', { name: '전송' }))
}

describe('RagLabApp', () => {
  it('keeps explicit mobile panel switching and one message composer', async () => {
    const user = userEvent.setup()
    render(<RagLabApp service={baseService()} />)

    const nav = screen.getByRole('navigation', { name: '모바일 패널' })
    await user.click(screen.getByRole('button', { name: '검사기 패널' }))

    expect(nav.parentElement).toHaveAttribute('data-mobile-view', 'inspector')
    expect(screen.getByLabelText('API 검사기')).toBeInTheDocument()
    expect(
      within(screen.getByLabelText('대화 및 결과')).getByRole('textbox', {
        name: '메시지',
      }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Fast 강제' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Deep 강제' })).not.toBeInTheDocument()
  })

  it('sends one route-free request and renders answer, evidence, and agent execution', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange(chatResponse({
        references: [
          {
            evidence_id: 'S1',
            source_type: 'calendar',
            document_id: 'opaque-1',
            title: '주간 회의',
            excerpt: '월요일 오전 10시',
          },
        ],
      })),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user)

    expect(await screen.findByText(/확인된 답변입니다/)).toBeInTheDocument()
    expect(screen.getByText('주간 회의')).toBeInTheDocument()
    expect(screen.getByText('일정/회의')).toBeInTheDocument()
    const resultPanel = screen.getByLabelText('대화 및 결과')
    expect(within(resultPanel).getByText('search_calendar')).toBeInTheDocument()
    expect(within(resultPanel).getByText('sufficient')).toBeInTheDocument()
    expect(within(resultPanel).getByText(/실제 검색 1회/)).toBeInTheDocument()
    expect(service.sendChat).toHaveBeenCalledWith(
      expect.not.objectContaining({ response_mode: expect.anything() }),
    )
    expect(screen.queryByText(/Router/)).not.toBeInTheDocument()
  })

  it('shows safe API failures in the result and request panels', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue({
      request: {
        method: 'POST',
        path: '/v1/chat',
        body: { user_id: 'kim', message: '질문' },
      },
      status: 503,
      durationMs: 12,
      receivedAt: '2026-08-17T00:00:00Z',
      error: {
        code: 'INDEX_UNAVAILABLE',
        message: '검색 서비스를 현재 사용할 수 없습니다.',
        retryable: true,
      },
    })

    render(<RagLabApp service={service} />)
    await submitQuestion(user)

    const resultPanel = screen.getByLabelText('대화 및 결과')
    expect(await within(resultPanel).findByRole('alert')).toHaveTextContent(
      'INDEX_UNAVAILABLE',
    )
    const requestForm = screen.getByRole('heading', { name: 'RAG 검증 콘솔' })
      .closest('form')
    expect(within(requestForm!).getByRole('alert')).toHaveTextContent(
      '검색 서비스를 현재 사용할 수 없습니다.',
    )
  })

  it('carries the server conversation id into the next turn', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat)
      .mockResolvedValueOnce(
        exchange(chatResponse({
          conversation_id: 'conversation-followup',
          answer: '첫 답변',
        })),
      )
      .mockResolvedValueOnce(
        exchange(chatResponse({
          conversation_id: 'conversation-followup',
          answer: '후속 답변',
        })),
      )

    render(<RagLabApp service={service} />)
    await submitQuestion(user)
    await screen.findByText('첫 답변')

    const composer = screen.getByRole('textbox', { name: '메시지' })
    await user.type(composer, '후속 질문')
    await user.click(screen.getByRole('button', { name: '전송' }))

    await waitFor(() => expect(service.sendChat).toHaveBeenCalledTimes(2))
    expect(service.sendChat).toHaveBeenLastCalledWith(
      expect.objectContaining({ conversation_id: 'conversation-followup' }),
    )
    expect(await screen.findByText('후속 답변')).toBeInTheDocument()
    expect(composer).toHaveValue('')
  })

  it('uses Enter to send, Shift+Enter for a newline, and blocks empty messages', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(exchange(chatResponse()))

    render(<RagLabApp service={service} />)
    await user.type(screen.getByLabelText('user_id'), 'kim')
    const composer = screen.getByRole('textbox', { name: '메시지' })
    expect(screen.getByRole('button', { name: '전송' })).toBeDisabled()

    await user.type(composer, '첫 줄')
    fireEvent.keyDown(composer, { key: 'Enter', shiftKey: true })
    expect(service.sendChat).not.toHaveBeenCalled()

    fireEvent.change(composer, { target: { value: '첫 줄\n둘째 줄' } })
    fireEvent.keyDown(composer, { key: 'Enter' })

    await waitFor(() => expect(service.sendChat).toHaveBeenCalledTimes(1))
    expect(service.sendChat).toHaveBeenCalledWith(
      expect.objectContaining({ message: '첫 줄\n둘째 줄' }),
    )
  })

  it('does not reuse a conversation id after the owner changes', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat)
      .mockResolvedValueOnce(
        exchange(chatResponse({
          conversation_id: 'conversation-kim',
          answer: '김 답변',
        })),
      )
      .mockResolvedValueOnce(
        exchange(chatResponse({
          conversation_id: 'conversation-lee',
          answer: '이 답변',
        })),
      )

    render(<RagLabApp service={service} />)
    await submitQuestion(user)
    await screen.findByText('김 답변')

    await user.clear(screen.getByLabelText('user_id'))
    await user.type(screen.getByLabelText('user_id'), 'lee')
    await user.type(screen.getByRole('textbox', { name: '메시지' }), '새 소유자 질문')
    await user.click(screen.getByRole('button', { name: '전송' }))

    await waitFor(() => expect(service.sendChat).toHaveBeenCalledTimes(2))
    expect(service.sendChat).toHaveBeenLastCalledWith(
      expect.not.objectContaining({ conversation_id: 'conversation-kim' }),
    )
  })
})
