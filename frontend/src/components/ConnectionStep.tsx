import { useState, type FormEvent } from 'react'

interface ConnectionStepProps {
  initialUserId?: string
  onConnect: (userId: string, password: string) => Promise<void>
}

export function ConnectionStep({
  initialUserId = '',
  onConnect,
}: ConnectionStepProps) {
  const [userId, setUserId] = useState(initialUserId)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [isConnecting, setIsConnecting] = useState(false)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setIsConnecting(true)

    try {
      await onConnect(userId, password)
    } catch (connectionError) {
      setError(
        connectionError instanceof Error
          ? connectionError.message
          : '계정 연결을 확인할 수 없습니다.',
      )
    } finally {
      setPassword('')
      setIsConnecting(false)
    }
  }

  return (
    <section className="wizard-panel" aria-labelledby="connection-title">
      <div className="panel-heading">
        <span className="eyebrow">Step 1</span>
        <h2 id="connection-title">Outlook 계정 연결</h2>
        <p>계정 정보를 확인한 뒤 선택할 수 있는 메일 폴더를 불러옵니다.</p>
      </div>

      <form className="connection-form" onSubmit={handleSubmit} noValidate>
        <label className="field">
          <span>사용자 ID</span>
          <input
            autoComplete="username"
            inputMode="email"
            name="userId"
            placeholder="user@company.com"
            value={userId}
            onChange={(event) => setUserId(event.target.value)}
          />
        </label>

        <label className="field">
          <span>비밀번호</span>
          <input
            autoComplete="current-password"
            name="password"
            placeholder="비밀번호 입력"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>

        <div className="security-note">
          <span className="security-mark" aria-hidden="true">
            ↗
          </span>
          <p>
            현재는 더미 연결입니다. 입력한 비밀번호는 연결 확인 후 상태나
            브라우저 저장소에 남기지 않습니다.
          </p>
        </div>

        {error && (
          <p className="form-message is-error" role="alert">
            {error}
          </p>
        )}

        <div className="form-actions is-end">
          <button className="button button-primary" disabled={isConnecting}>
            {isConnecting ? '폴더 불러오는 중…' : '연결하고 다음'}
          </button>
        </div>
      </form>
    </section>
  )
}
