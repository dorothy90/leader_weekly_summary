import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import App from './App'
import { DummyMailSourceService } from './services/mailSourceService'
import type { RagApiClient } from './services/ragApiService'

const fakeRagService = {
  getHealth: async () => ({
    request: { method: 'GET' as const, path: '/health' },
    response: { status: 'ok' as const },
    status: 200,
    durationMs: 4,
    receivedAt: new Date(0).toISOString(),
  }),
  getReadiness: async () => ({
    request: { method: 'GET' as const, path: '/ready' },
    response: { status: 'ready' as const, dependencies: { mongo: 'ready' } },
    status: 200,
    durationMs: 5,
    receivedAt: new Date(0).toISOString(),
  }),
} as Pick<RagApiClient, 'getHealth' | 'getReadiness'> as RagApiClient

describe('Outlook folder setup wizard', () => {
  it('navigates between folder setup and the RAG Lab without replacing the app', async () => {
    const user = userEvent.setup()
    window.history.replaceState({}, '', '/rag')
    render(<App ragService={fakeRagService} />)

    expect(
      screen.getByRole('heading', { name: 'RAG 검증 콘솔' }),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Folder setup' }))
    expect(window.location.pathname).toBe('/')
    expect(
      screen.getByRole('heading', { name: 'Outlook 계정 연결' }),
    ).toBeInTheDocument()
  })

  it('connects, selects folders, saves, and shows a password-free summary', async () => {
    const user = userEvent.setup()
    render(<App service={new DummyMailSourceService(window.localStorage)} />)

    await user.type(screen.getByLabelText('사용자 ID'), 'user@company.com')
    await user.type(screen.getByLabelText('비밀번호'), 'super-secret')
    await user.click(screen.getByRole('button', { name: '연결하고 다음' }))

    expect(
      await screen.findByRole('heading', { name: '수집할 폴더 선택' }),
    ).toBeInTheDocument()
    await user.click(screen.getByLabelText(/Project Alpha/))
    await user.click(
      screen.getByRole('button', { name: /선택한 3개 폴더 저장/ }),
    )

    expect(
      await screen.findByRole('heading', { name: '설정이 저장되었습니다' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Project Alpha')).toBeInTheDocument()
    expect(screen.queryByText('super-secret')).not.toBeInTheDocument()
    expect(
      window.localStorage.getItem('weekly-mail:mail-source'),
    ).not.toContain('super-secret')
  })

  it('filters folders and allows returning to the connection step', async () => {
    const user = userEvent.setup()
    render(<App service={new DummyMailSourceService(window.localStorage)} />)

    await user.type(screen.getByLabelText('사용자 ID'), 'user@company.com')
    await user.type(screen.getByLabelText('비밀번호'), 'dummy')
    await user.click(screen.getByRole('button', { name: '연결하고 다음' }))
    await user.type(await screen.findByLabelText('폴더 검색'), 'weekly')

    expect(screen.getByText('Weekly Reports')).toBeInTheDocument()
    expect(screen.queryByText('Project Alpha')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '이전' }))
    expect(
      screen.getByRole('heading', { name: 'Outlook 계정 연결' }),
    ).toBeInTheDocument()
  })

  it('shows validation guidance without leaving the current step', async () => {
    const user = userEvent.setup()
    render(<App service={new DummyMailSourceService(window.localStorage)} />)

    await user.click(screen.getByRole('button', { name: '연결하고 다음' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      '사용자 ID를 입력해 주세요.',
    )
    expect(
      screen.getByRole('heading', { name: 'Outlook 계정 연결' }),
    ).toBeInTheDocument()
  })

  it('exposes the wizard and folder choices with semantic progress markup', async () => {
    const user = userEvent.setup()
    render(<App service={new DummyMailSourceService(window.localStorage)} />)

    expect(screen.getByText('계정 연결').closest('li')).toHaveAttribute(
      'aria-current',
      'step',
    )
    await user.type(screen.getByLabelText('사용자 ID'), 'user@company.com')
    await user.type(screen.getByLabelText('비밀번호'), 'dummy')
    await user.click(screen.getByRole('button', { name: '연결하고 다음' }))

    expect(
      await screen.findByRole('group', { name: 'Outlook 폴더' }),
    ).toHaveProperty('tagName', 'FIELDSET')
    expect(screen.getByText('폴더 선택').closest('li')).toHaveAttribute(
      'aria-current',
      'step',
    )
  })
})
