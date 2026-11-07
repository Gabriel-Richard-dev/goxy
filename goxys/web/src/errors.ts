export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, body: unknown, msg: string) {
    super(msg)
    this.status = status
    this.body = body
  }
}
