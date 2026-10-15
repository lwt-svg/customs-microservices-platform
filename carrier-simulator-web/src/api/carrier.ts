/**
 * 本文件是门户与“独立模拟承运人服务”的唯一 HTTP 边界。
 *
 * 页面组件不直接写 fetch：这样读代码时可先看这里理解接口，再看 App.vue 理解
 * “列表 → 处理 → 决策 → 回调重试”的用户操作。
 */
export type CarrierBookingStatus = 'RECEIVED' | 'PROCESSING' | 'CONFIRMED' | 'REJECTED'

export interface CarrierBooking {
  id: number
  carrierRequestId: string
  applicationNumber: string
  status: CarrierBookingStatus
  originPort: string
  destinationPort: string
  transportMode: string
  containerType: string
  requestedEtd: string | null
  bookingReference: string | null
  rejectionReasonCode: string | null
  createdAt: string
  updatedAt: string
  timeline: CarrierTimelineEvent[]
}

export interface CarrierTimelineEvent {
  eventType: string
  occurredAt: string
  description: string
}

export interface CallbackDelivery {
  id: number
  callbackId: string
  bookingId: number
  eventType: string
  status: 'PENDING' | 'SENDING' | 'RETRY_WAIT' | 'DELIVERED' | 'DEAD'
  attemptCount: number
  nextAttemptAt: string | null
  lastError: string | null
  createdAt: string
  deliveredAt: string | null
}

interface ListResponse<T> {
  items?: T[]
  data?: T[]
}

interface RawBooking {
  id: number
  carrier_request_id: string
  application_number: string
  origin_port: string
  destination_port: string
  transport_mode: string
  container_type: string
  requested_etd: string | null
  status: CarrierBookingStatus
  booking_reference: string | null
  rejection_reason_code: string | null
  created_at: string
  updated_at: string
}

interface RawBookingEvent {
  id: string
  event_type: string
  previous_status: CarrierBookingStatus | null
  current_status: CarrierBookingStatus | null
  actor_label: string
  occurred_at: string
}

interface RawCallbackDelivery {
  id: number
  callback_id: string
  booking_id: number
  event_type: string
  status: CallbackDelivery['status']
  attempt_count: number
  next_attempt_at: string | null
  last_error: string | null
  delivered_at: string | null
  created_at: string
}

interface RawBookingDetail {
  booking: RawBooking
  events: RawBookingEvent[]
  callback_deliveries: RawCallbackDelivery[]
}

function messageFrom(body: unknown, fallback: string): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') {
      return detail
    }
  }
  if (typeof body === 'object' && body !== null && 'message' in body) {
    const message = (body as { message?: unknown }).message
    if (typeof message === 'string') {
      return message
    }
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  const body: unknown = response.status === 204 ? undefined : await response.json().catch(() => undefined)
  if (!response.ok) {
    throw new Error(messageFrom(body, `请求失败（HTTP ${response.status}）`))
  }
  return body as T
}

function unwrapList<T>(body: T[] | ListResponse<T>): T[] {
  if (Array.isArray(body)) {
    return body
  }
  return body.items ?? body.data ?? []
}

function toBooking(raw: RawBooking, events: RawBookingEvent[] = []): CarrierBooking {
  return {
    id: raw.id,
    carrierRequestId: raw.carrier_request_id,
    applicationNumber: raw.application_number,
    status: raw.status,
    originPort: raw.origin_port,
    destinationPort: raw.destination_port,
    transportMode: raw.transport_mode,
    containerType: raw.container_type,
    requestedEtd: raw.requested_etd,
    bookingReference: raw.booking_reference,
    rejectionReasonCode: raw.rejection_reason_code,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    timeline: events.map((event) => ({
      eventType: event.event_type,
      occurredAt: event.occurred_at,
      description: `${event.actor_label}：${event.previous_status ?? '创建'} → ${event.current_status ?? event.event_type}`,
    })),
  }
}

function toCallbackDelivery(raw: RawCallbackDelivery): CallbackDelivery {
  return {
    id: raw.id,
    callbackId: raw.callback_id,
    bookingId: raw.booking_id,
    eventType: raw.event_type,
    status: raw.status,
    attemptCount: raw.attempt_count,
    nextAttemptAt: raw.next_attempt_at,
    lastError: raw.last_error,
    createdAt: raw.created_at,
    deliveredAt: raw.delivered_at,
  }
}

export async function getBookings(filters: {
  status?: CarrierBookingStatus | ''
  keyword?: string
}): Promise<CarrierBooking[]> {
  const query = new URLSearchParams()
  if (filters.status) query.set('status', filters.status)
  if (filters.keyword?.trim()) query.set('keyword', filters.keyword.trim())
  const suffix = query.size > 0 ? `?${query.toString()}` : ''
  const body = await request<RawBooking[] | ListResponse<RawBooking>>(`/api/v1/portal/bookings${suffix}`)
  return unwrapList(body).map((item) => toBooking(item))
}

export async function getBooking(id: number): Promise<CarrierBooking> {
  const detail = await request<RawBookingDetail>(`/api/v1/portal/bookings/${id}`)
  return toBooking(detail.booking, detail.events)
}

export async function startProcessing(id: number): Promise<CarrierBooking> {
  await request<unknown>(`/api/v1/portal/bookings/${id}/process`, { method: 'POST' })
  return getBooking(id)
}

export async function makeDecision(
  id: number,
  decision: { decision: 'CONFIRMED' } | { decision: 'REJECTED'; rejectionReasonCode: string },
): Promise<CarrierBooking> {
  const body =
    decision.decision === 'CONFIRMED'
      ? { decision: 'CONFIRMED' }
      : { decision: 'REJECTED', rejection_reason_code: decision.rejectionReasonCode }
  await request<unknown>(`/api/v1/portal/bookings/${id}/decision`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return getBooking(id)
}

export async function getCallbackDeliveries(): Promise<CallbackDelivery[]> {
  const body = await request<RawCallbackDelivery[] | ListResponse<RawCallbackDelivery>>('/api/v1/portal/callback-deliveries')
  return unwrapList(body).map((item) => toCallbackDelivery(item))
}

export async function retryCallbackDelivery(id: number): Promise<CallbackDelivery> {
  await request<unknown>(`/api/v1/portal/callback-deliveries/${id}/retry`, { method: 'POST' })
  const deliveries = await getCallbackDeliveries()
  const delivery = deliveries.find((item) => item.id === id)
  if (!delivery) throw new Error('回调已重试，但没有在刷新后的列表中找到记录')
  return delivery
}
