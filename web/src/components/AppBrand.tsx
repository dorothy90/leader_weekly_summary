import { Link } from 'react-router-dom'

export function AppBrand() {
  return (
    <Link className="brand" to="/wiki/docs">
      <span className="brand-mark" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>
        <strong>Yield Knowledge</strong>
        <small>mail intelligence</small>
      </span>
    </Link>
  )
}
