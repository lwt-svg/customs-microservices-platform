/**
 * Java BFF 的统一响应外壳。
 *
 * @template T data 字段中真正的业务数据类型。
 */
export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

/** Java BFF 健康检查返回的数据。 */
export interface HealthData {
  status: string
  service: string
}

/** Java BFF 转发的 Python LINK 服务健康状态。 */
export interface LinkHealthData {
  status: string
  service: string
  environment: string
}

/** Python LINK 订舱申请经 Java BFF 转换后的平台视图。 */
export interface LinkBookingApplication {
  id: number
  applicationNumber: string
  bookingReference: string | null
  originPort: string
  destinationPort: string
  transportMode: string
  containerType: string
  requestedEtd: string | null
  bookingDetails: LinkBookingDetails | null
  status: 'DRAFT' | 'SUBMITTED' | 'CONFIRMED' | 'REJECTED' | 'CANCELLED'
  createdAt: string
  updatedAt: string
}

/** 装箱单中的一条商品明细。 */
export interface LinkBookingItem {
  itemCode: string | null
  lotNumber: string | null
  poNumber: string | null
  description: string | null
  quantity: number | null
  cartons: number | null
  netWeightKg: number | null
  grossWeightKg: number | null
}

/** 一个包组内的 PO 分配。 */
export interface LinkBookingPoAllocation {
  poNumber: string
  quantity: number | null
}

/** PDF 按包组汇总出的托数、箱数、重量、体积和 PO。 */
export interface LinkBookingPackage {
  description: string | null
  pallets: number | null
  cartons: number | null
  quantity: number | null
  netWeightKg: number | null
  grossWeightKg: number | null
  cbm: number | null
  poAllocations: LinkBookingPoAllocation[]
}

/**
 * 从装箱单识别的业务明细。
 *
 * 起运地、目的地、运输方式、箱型和计划日期不属于这份装箱单的可靠字段，页面会把它们
 * 单独列为“业务补充”，避免把默认值伪装成 PDF 识别结果。
 */
export interface LinkBookingDetails {
  invoiceNumber: string | null
  documentDate: string | null
  ciqNumber: string | null
  shipper: string | null
  consignee: string | null
  notifyParty: string | null
  marks: string | null
  totalPallets: number | null
  totalCartons: number | null
  totalQuantity: number | null
  totalCbm: number | null
  totalNetWeightKg: number | null
  totalGrossWeightKg: number | null
  items: LinkBookingItem[]
  packages: LinkBookingPackage[]
  checks: string[]
}

/**
 * Python LINK 保存的操作流水经 Java BFF 转换后的平台视图。
 *
 * eventType 保留为字符串，而不是让 Vue 重复维护 Python 的完整事件机；页面只把当前已知
 * 事件翻译成中文，遇到未来新增事件时仍可安全展示原始类型。
 */
export interface LinkBookingApplicationEvent {
  id: number
  eventType: string
  previousStatus: LinkBookingApplication['status'] | null
  currentStatus: LinkBookingApplication['status']
  bookingReference: string | null
  actorLabel: string
  occurredAt: string
}

/** 当前申请快照与倒序审计流水组成的详情响应。 */
export interface LinkBookingApplicationDetail {
  application: LinkBookingApplication
  events: LinkBookingApplicationEvent[]
  carrierSubmission: LinkCarrierSubmission | null
}

/** LINK→模拟承运人的技术投递状态，与申请业务状态分开显示。 */
export interface LinkCarrierSubmission {
  status:
    | 'PENDING'
    | 'SENDING'
    | 'RETRY_WAIT'
    | 'ACCEPTED'
    | 'PROCESSING'
    | 'CONFIRMED'
    | 'REJECTED'
    | 'DEAD'
    | 'CANCELLED'
  attemptCount: number
  lastErrorMessage: string | null
  canRetry: boolean
  canCancel: boolean
  carrierRequestId: string | null
}

/**
 * 页面只能请求可能作为“下一步”的状态。
 *
 * DRAFT 是创建时由 Python 赋予的初始状态，不是一个可被页面主动迁移到的目标状态。
 * Python 仍会再次验证真正允许的转换，前端按钮只是为了避免给用户展示明显无效的操作。
 */
export type LinkBookingTargetStatus = 'SUBMITTED' | 'CANCELLED'

/**
 * 创建 LINK 订舱草稿时，Vue 交给 Java BFF 的字段。
 *
 * 这里刻意使用 camelCase；Java BFF 会在服务内部转换成 Python 所需的 snake_case。
 */
export interface CreateLinkBookingApplicationInput {
  applicationNumber: string
  originPort: string
  destinationPort: string
  transportMode: string
  containerType: string
  requestedEtd: string | null
}

export type LinkBookingCandidateConfidence = 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE'

/** PDF 中提取的候选值；证据和置信度只用于人工判断，不是业务真值。 */
export interface LinkBookingCandidateField {
  value: string | null
  evidence: string | null
  confidence: LinkBookingCandidateConfidence
}

export interface LinkBookingDocumentImport {
  id: number
  status: 'RECEIVED' | 'PARSED' | 'FAILED' | 'CONFIRMED'
  originalFilename: string
  fileSizeBytes: number
  parserVersion: string
  candidates: {
    applicationNumber: LinkBookingCandidateField
    originPort: LinkBookingCandidateField
    destinationPort: LinkBookingCandidateField
    transportMode: LinkBookingCandidateField
    containerType: LinkBookingCandidateField
    requestedEtd: LinkBookingCandidateField
    bookingDetails: LinkBookingDetails | null
  }
  createdApplicationNumber: string | null
  createdAt: string
  updatedAt: string
}

export interface ConfirmLinkBookingDocumentImportInput extends CreateLinkBookingApplicationInput {
  bookingDetails: LinkBookingDetails | null
}

/** 组织列表中当前页面需要使用的数据。 */
export interface Organization {
  id: number
  organizationCode: string
  organizationName: string
  status: string
  createdAt: string
  updatedAt: string
}

/** 用户列表中当前页面需要使用的数据。 */
export interface PlatformUser {
  id: number
  username: string
  displayName: string
  organizationId: number
  organizationCode: string
  organizationName: string
  status: string
  createdAt: string
  updatedAt: string
}

export interface PlatformRole {
  id: number
  roleCode: string
  roleName: string
  status: string
  createdAt: string
  updatedAt: string
}

export interface PlatformPermission {
  id: number
  permissionCode: string
  permissionName: string
  status: string
  createdAt: string
  updatedAt: string
}

/** Java BFF 返回的菜单树节点。 */
export interface PlatformMenu {
  id: number
  menuCode: string
  menuName: string
  routePath: string
  icon: string | null
  sortOrder: number
  status: string
  parentId: number | null
  permissionCode: string | null
  createdAt: string
  updatedAt: string
  children: PlatformMenu[]
}

/** 装载主数据的来源类型；SYNTHETIC_DEMO 只用于脱敏学习数据。 */
export type LoadPlanningSourceType =
  | 'SYNTHETIC_DEMO'
  | 'MANUAL'
  | 'ENTERPRISE_HISTORICAL'
  | 'DOCUMENT_IMPORT'

export interface LoadPlanningTaskSummary {
  id: number
  taskNumber: string
  taskName: string
  status: string
  cargoItemCount: number
  cargoBoxCount: number
  palletSpecCount: number
  createdAt: string
  updatedAt: string
}

export interface LoadPlanningCargoItem {
  id: number
  itemCode: string
  skuCode: string | null
  cargoName: string
  quantity: number
  lengthMm: number
  widthMm: number
  heightMm: number
  grossWeightKg: number
  maxStackLayers: number | null
  maxTopLoadKg: number | null
  stackable: boolean | null
  keepUpright: boolean | null
  fragile: boolean | null
  sourceType: LoadPlanningSourceType
  sourceReference: string | null
  sourceLocator: string | null
  confidence: number | null
  verified: boolean
}

export interface LoadPlanningPalletSpec {
  id: number
  palletCode: string
  palletName: string
  quantity: number
  lengthMm: number
  widthMm: number
  heightMm: number
  tareWeightKg: number
  maxLoadKg: number | null
  maxTotalHeightMm: number | null
  sourceType: LoadPlanningSourceType
  sourceReference: string | null
  sourceLocator: string | null
  confidence: number | null
  verified: boolean
}

export interface LoadPlanningTask {
  id: number
  taskNumber: string
  taskName: string
  description: string | null
  status: string
  units: {
    dimension: string
    weight: string
  }
  cargoItems: LoadPlanningCargoItem[]
  palletSpecs: LoadPlanningPalletSpec[]
  createdAt: string
  updatedAt: string
}

/** 创建接口不包含由 Python 数据库生成的 id、状态和时间。 */
export interface CreateLoadPlanningCargoItemInput
  extends Omit<LoadPlanningCargoItem, 'id'> {}

export interface CreateLoadPlanningPalletSpecInput
  extends Omit<LoadPlanningPalletSpec, 'id'> {}

export interface CreateLoadPlanningTaskInput {
  taskNumber: string
  taskName: string
  description: string | null
  cargoItems: CreateLoadPlanningCargoItemInput[]
  palletSpecs: CreateLoadPlanningPalletSpecInput[]
}

/**
 * RULES_V3 从 Packing List 中提取的一种箱规候选。
 *
 * 这些字段仍然只是“待人工复核的文档候选”，不是已经确认的货物主数据。
 */
export interface PackingListCargoCandidate {
  cargoKey: string
  itemCode: string
  itemCodes: string[]
  description: string
  cartonQuantity: number
  unitsPerCarton: number
  cartonNetWeightKg: number
  cartonGrossWeightKg: number
  lengthMm: number
  widthMm: number
  heightMm: number
  sourceLocator: string
  confidence: number
}

/**
 * PDF 中记载的历史装托模式。
 *
 * loadedHeightMm 是“历史成托后的总高度”，不能当作托盘本身的空托高度。
 */
export interface HistoricalPalletPattern {
  cargoKey: string | null
  cartonsPerLayer: number
  patternCartonCount: number
  palletQuantity: number | null
  palletTareWeightKg: number
  loadedLengthMm: number
  loadedWidthMm: number
  loadedHeightMm: number
  cbm: number
  sourceLocator: string
}

/** PL 解析只返回预览；用户复核并补齐未知字段后，才可以创建装载任务。 */
export interface PackingListPreview {
  parserVersion: string
  declaredTotalPallets: number | null
  declaredTotalCbm: number | null
  cargoCandidates: PackingListCargoCandidate[]
  historicalPalletPatterns: HistoricalPalletPattern[]
  checks: string[]
}

/**
 * 第 23.4 章的装载规划智能体不是聊天机器人。
 *
 * 它把“检查输入、追问缺失约束、运行确定性算法、评价多套方案、等待人工审批”保存为
 * 可恢复状态。Python 内部现已使用 LangGraph 编排人工断点；大模型仍不是运行依赖，
 * 也不会参与坐标、安全约束、审批或正式版本计算。
 */
export type LoadPlanningAgentStatus =
  | 'AWAITING_INPUT'
  | 'READY_FOR_REVIEW'
  | 'APPROVED'
  | 'REJECTED'
  | 'FAILED'

/**
 * Python 状态机当前所处的执行步骤。
 *
 * 不要把它与最终的 status 混为一谈：例如 AWAITING_INPUT 是运行状态，
 * AWAITING_CONSTRAINTS 才是它暂停等待人工回答时所在的步骤。
 */
export type LoadPlanningAgentCurrentStep =
  | 'CHECKING_CONSTRAINTS'
  | 'AWAITING_CONSTRAINTS'
  | 'GENERATING_PLANS'
  | 'AWAITING_REVIEW'
  | 'FAILED'
  | 'COMPLETED'

export type LoadPlanningAgentQuestionValueType =
  | 'NUMBER'
  | 'INTEGER'
  | 'BOOLEAN'
  | 'TEXT'

export type LoadPlanningStrategyCode = 'STABLE' | 'BALANCED' | 'UTILIZATION'

export interface LoadPlanningAgentQuestion {
  key: string
  label: string
  valueType: LoadPlanningAgentQuestionValueType
  unit: string | null
  reason: string
  required: boolean
  minValue: number | null
  maxValue: number | null
}

export interface LoadPlanningAgentEvent {
  sequence: number
  eventType: string
  title: string
  detail: string
  createdAt: string
}

export interface LoadPlanningPlacement {
  itemCode: string
  boxIndex: number
  /**
   * 箱体左下后角在托盘局部坐标系内的位置，单位 mm。
   *
   * zMm=0 是空托盘的底面，而不是托盘上表面；因此首层箱体的 zMm
   * 等于 palletSpec.heightMm。Three.js 仅映射该权威坐标，不能再次叠加空托高度。
   */
  xMm: number
  yMm: number
  zMm: number
  lengthMm: number
  widthMm: number
  heightMm: number
  orientation: string
}

export interface LoadPlanningPlanPallet {
  palletIndex: number
  palletCode: string
  loadedHeightMm: number
  grossWeightKg: number
  boxCount: number
  placements: LoadPlanningPlacement[]
}

export interface LoadPlanningPlanMetrics {
  totalPallets: number
  totalBoxes: number
  unplacedBoxes: number
  volumeUtilization: number
  weightUtilization: number
  averageLoadedHeightMm: number
  maxLoadedHeightMm: number
  totalGrossWeightKg: number
}

export interface LoadPlanningAgentPlan {
  /** Python 以 UUID 字符串生成的候选方案标识，不是数据库自增数字。 */
  planId: string
  planCode: string
  strategyCode: LoadPlanningStrategyCode
  strategyName: string
  algorithmVersion: string
  scoreVersion: string
  feasible: boolean
  score: number
  summary: string
  metrics: LoadPlanningPlanMetrics
  warnings: string[]
  pallets: LoadPlanningPlanPallet[]
}

export interface LoadPlanningAgentRun {
  runId: string
  parentRunId: string | null
  taskNumber: string
  status: LoadPlanningAgentStatus
  currentStep: LoadPlanningAgentCurrentStep
  runVersion: number
  selectedPalletCode: string | null
  selectedPlanCode: string | null
  failureCode: string | null
  failureMessage: string | null
  questions: LoadPlanningAgentQuestion[]
  events: LoadPlanningAgentEvent[]
  plans: LoadPlanningAgentPlan[]
}

/**
 * 候选方案的辅助解读。
 *
 * 这些文字只帮助业务人员更快理解结构化结果，不会回写方案坐标、评分、审批状态或
 * 正式版本。degraded=true 表示首选解读服务不可用，本次已使用基础规则说明。
 */
export interface LoadPlanningPlanInsight {
  runId: string
  planCode: string
  provider: 'OPENAI' | 'CODEX_CLI' | 'RULE_BASED'
  model: string | null
  degraded: boolean
  generatedAt: string
  summary: string
  tradeoffs: string[]
  risks: string[]
  nextChecks: string[]
  disclaimer: string
}

export type LoadPlanningAgentAnswerValue = string | number | boolean

export interface CreateLoadPlanningAgentRunInput {
  selectedPalletCode: string | null
  strategyCodes: LoadPlanningStrategyCode[]
}

export interface ResumeLoadPlanningAgentRunInput {
  answers: Array<{
    key: string
    value: LoadPlanningAgentAnswerValue
  }>
  expectedRunVersion?: number
}

export interface DecideLoadPlanningAgentRunInput {
  decision: 'APPROVE' | 'REJECT'
  planCode: string | null
  comment: string | null
  expectedRunVersion?: number
}

export interface ReplanLoadPlanningAgentRunInput {
  reason: string
  selectedPalletCode?: string | null
  strategyCodes?: LoadPlanningStrategyCode[]
}

/**
 * 正式装载方案版本与 AgentRun 是两个不同聚合。
 *
 * AgentRun 记录“怎样试算、补问、比较并批准候选”；LoadPlanVersion 记录“哪一个已批准
 * 候选在什么时间被正式发布”。正式版本一旦生成，后续页面、导出和现场执行只能读取这里
 * 冻结的 snapshot，不能重新运行算法后覆盖 V1。
 */
export interface LoadPlanVersionEvent {
  sequence: number
  eventType: string
  actorSubject: string
  actorDisplayName: string
  actorSource: string
  requestId: string
  detail: string
  occurredAt: string
}

export interface LoadPlanVersion {
  versionId: string
  status: 'LOCKED'
  taskNumber: string
  versionNumber: number
  sourceRunId: string
  sourcePlanId: string
  planCode: string
  selectedPalletCode: string
  supersedesVersionId: string | null
  sourceRunVersion: number
  snapshotSchemaVersion: string
  inputSnapshotHash: string
  constraintSnapshotHash: string
  planSnapshotHash: string
  algorithmVersion: string
  scoreVersion: string
  approvalActorSubject: string
  approvalActorDisplayName: string
  approvalActorSource: string
  approvedAt: string
  approvalComment: string | null
  lockedBySubject: string
  lockedByDisplayName: string
  lockedBySource: string
  lockReason: string
  lockedAt: string
  inputSnapshot: Record<string, unknown>
  constraintSnapshot: Record<string, unknown>
  planSnapshot: Record<string, unknown>
  events: LoadPlanVersionEvent[]
}

export interface LockLoadPlanVersionInput {
  expectedRunVersion: number
  expectedCurrentVersionNumber: number
  lockReason: string
}

/**
 * 访问 Java BFF 并拆开统一响应外壳。
 *
 * 浏览器只请求相对路径 /api，开发环境由 Vite 代理到 127.0.0.1:8080。
 */
async function request<T>(path: string): Promise<T> {
  return requestWithOptions<T>(path)
}

/**
 * 发送带有可选请求配置的 Java BFF 请求。
 *
 * 创建类接口通过 options 传入 method 和 body，查询接口仍可使用上方的 request。
 */
async function requestWithOptions<T>(
  path: string,
  options?: RequestInit,
  errorMessages?: Partial<Record<number, string>>,
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const friendlyHttpErrors: Record<number, string> = {
      400: '提交的数据格式不正确，请检查后重试。',
      404: '请求的数据不存在。',
      409: '组织编码已存在，请更换编码。',
      500: 'Java BFF 处理失败，请查看后端日志。',
    }
    let errorMessage =
      errorMessages?.[response.status] ||
      friendlyHttpErrors[response.status] ||
      `Java BFF 返回 HTTP ${response.status}`

    try {
      const errorBody = (await response.json()) as {
        detail?: string
        message?: string
        error?: string
      }
      const serverMessage = errorBody.detail || errorBody.message || errorBody.error
      // Spring Boot 默认错误页常把上游失败压缩成这些英文状态文字。它们不是对学习者有帮助的
      // 业务说明，因此保留当前接口为 404/409/502/503 预先准备的中文提示。
      const genericMessages = [
        'Bad Request',
        'Payload Too Large',
        'Unsupported Media Type',
        'Unprocessable Entity',
        'Not Found',
        'Conflict',
        'Internal Server Error',
        'Bad Gateway',
        'Service Unavailable',
      ]

      if (serverMessage && !genericMessages.includes(serverMessage)) {
        errorMessage = serverMessage
      }
    } catch {
      // 某些错误响应可能没有 JSON 正文，此时保留 HTTP 状态提示。
    }

    throw new Error(errorMessage)
  }

  const result = (await response.json()) as ApiResponse<T>

  // HTTP 2xx 只表示请求到达 Java BFF 并得到正常 HTTP 响应；平台仍可能通过 code
  // 表示业务失败。页面只接收真正的 data，不需要重复拆 ApiResponse 外壳。
  if (result.code !== 0) {
    throw new Error(result.message || 'Java BFF 返回业务错误')
  }

  return result.data
}

/** 查询 Java BFF 自定义健康检查。 */
export function getPlatformHealth(): Promise<HealthData> {
  return request<HealthData>('/api/v1/platform/health')
}

/** 通过 Java BFF 查询 Python LINK 服务健康状态。 */
export function getLinkBookingHealth(): Promise<LinkHealthData> {
  return request<LinkHealthData>('/api/v1/platform/link-booking/health')
}

/**
 * 通过 Java BFF 和 Nacos 查询 Python LINK 中的订舱申请。
 *
 * Vue 不保存也不拼装 Python 地址；服务实例地址只存在于 Java BFF 运行期。
 */
export function getLinkBookingApplications(): Promise<LinkBookingApplication[]> {
  return request<LinkBookingApplication[]>('/api/v1/platform/link-booking/applications')
}

/**
 * 查询一份申请的当前快照与操作流水。
 *
 * 列表中的行可能在别的浏览器标签页被更新，因此详情抽屉重新请求 BFF，而不是从当前列表
 * 推导历史记录。
 */
export function getLinkBookingApplicationDetail(
  applicationNumber: string,
): Promise<LinkBookingApplicationDetail> {
  return requestWithOptions<LinkBookingApplicationDetail>(
    `/api/v1/platform/link-booking/applications/${encodeURIComponent(applicationNumber)}/detail`,
    undefined,
    {
      400: '订舱申请编号不正确。',
      404: '该订舱申请不存在或已被移除，请刷新列表。',
      502: 'Java BFF 暂时无法读取 Python LINK 订舱详情。',
      503: 'Nacos 中没有可用的 Python LINK 实例。',
    },
  )
}

/**
 * 创建 LINK 订舱草稿。
 *
 * 浏览器只向 Java BFF 发送一次 camelCase JSON；Java 再转换并转发给 Python。
 */
export function createLinkBookingApplication(
  input: CreateLinkBookingApplicationInput,
): Promise<LinkBookingApplication> {
  return requestWithOptions<LinkBookingApplication>(
    '/api/v1/platform/link-booking/applications',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    {
      400: '请检查申请编号、港口、箱型和日期格式。',
      409: '申请编号已经存在，请更换 DEMO 编号。',
      502: 'Java BFF 暂时无法调用 Python LINK，请稍后重试。',
      503: 'Nacos 中没有可用的 Python LINK 实例。',
    },
  )
}

/**
 * 上传文本型 PDF 并获取候选字段。
 *
 * 不要手动设置 Content-Type；浏览器会为 FormData 自动生成 multipart boundary。
 */
export function uploadLinkBookingDocument(file: File): Promise<LinkBookingDocumentImport> {
  const body = new FormData()
  body.append('file', file)
  return requestWithOptions<LinkBookingDocumentImport>(
    '/api/v1/platform/link-booking/document-imports',
    { method: 'POST', body },
    {
      400: '请选择有效的非空 PDF 文件。',
      413: 'PDF 不能超过 5MB。',
      415: '只允许上传 PDF 文件。',
      422: 'PDF 加密、损坏或没有可提取的文本。',
      502: 'Java BFF 暂时无法调用 Python LINK 解析 PDF。',
      503: 'Nacos 中没有可用的 Python LINK 实例。',
    },
  )
}

/** 用人工复核后的完整字段确认导入，并由 Python 原子创建 DRAFT。 */
export function confirmLinkBookingDocumentImport(
  documentId: number,
  input: ConfirmLinkBookingDocumentImportInput,
): Promise<LinkBookingApplication> {
  return requestWithOptions<LinkBookingApplication>(
    `/api/v1/platform/link-booking/document-imports/${documentId}/confirm`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    {
      400: '请检查人工复核后的字段。',
      404: 'PDF 导入记录不存在，请重新上传。',
      409: '申请编号重复、导入已确认或发生并发确认，请刷新后检查。',
      422: '人工复核字段不符合 LINK 接口要求。',
      502: 'Java BFF 暂时无法调用 Python LINK 创建草稿。',
      503: 'Nacos 中没有可用的 Python LINK 实例。',
    },
  )
}

/**
 * 让 Java BFF 请求 Python LINK 执行状态机转换。
 *
 * 成功时必须使用 Python 返回的完整申请对象替换页面旧行，不能在浏览器中自行改 status；
 * DRAFT → SUBMITTED 由七步页面的业务 VALIDATE 触发；最终确认或拒绝仍来自可选的异步扩展。
 */
export function changeLinkBookingApplicationStatus(
  applicationNumber: string,
  targetStatus: LinkBookingTargetStatus,
): Promise<LinkBookingApplication> {
  return requestWithOptions<LinkBookingApplication>(
    `/api/v1/platform/link-booking/applications/${encodeURIComponent(applicationNumber)}/status`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targetStatus }),
    },
    {
      400: '状态参数不正确，请刷新页面后重试。',
      404: '该订舱申请不存在或已被移除，请刷新列表。',
      409: '当前订舱状态不允许此操作，请刷新列表后重试。',
      502: 'Java BFF 暂时无法调用 Python LINK，请稍后重试。',
      503: 'Nacos 中没有可用的 Python LINK 实例。',
    },
  )
}

/** 对进入 DEAD 的 LINK→承运人投递任务发起人工重试，继续复用原幂等键。 */
export function retryLinkCarrierSubmission(
  applicationNumber: string,
): Promise<LinkCarrierSubmission> {
  return requestWithOptions<LinkCarrierSubmission>(
    `/api/v1/platform/link-booking/applications/${encodeURIComponent(applicationNumber)}/carrier-submission/retry`,
    { method: 'POST' },
    {
      404: '申请或承运人提交任务不存在。',
      409: '当前投递状态不允许重试，请刷新详情。',
      502: 'Java BFF 暂时无法调用 Python LINK 重试投递。',
      503: 'Nacos 中没有可用的 Python LINK 实例。',
    },
  )
}

/** 通过 Java BFF 查询 Python 装载规划服务的任务摘要。 */
export function getLoadPlanningTasks(): Promise<LoadPlanningTaskSummary[]> {
  return requestWithOptions<LoadPlanningTaskSummary[]>(
    '/api/v1/platform/load-planning/tasks',
    undefined,
    {
      502: 'Java BFF 暂时无法查询 Python 装载任务。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/** 创建任务聚合；浏览器只发送 camelCase 和明确的 mm/kg 字段。 */
export function createLoadPlanningTask(
  input: CreateLoadPlanningTaskInput,
): Promise<LoadPlanningTask> {
  return requestWithOptions<LoadPlanningTask>(
    '/api/v1/platform/load-planning/tasks',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    {
      400: '请检查任务编号、箱规、托盘规格、单位和来源信息。',
      409: '装载任务编号已经存在，请更换编号。',
      502: 'Java BFF 暂时无法调用 Python 创建装载任务。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/** 详情始终重新读取 Python 权威数据，不从列表摘要推导箱规。 */
export function getLoadPlanningTask(taskNumber: string): Promise<LoadPlanningTask> {
  return requestWithOptions<LoadPlanningTask>(
    `/api/v1/platform/load-planning/tasks/${encodeURIComponent(taskNumber)}`,
    undefined,
    {
      400: '装载任务编号不正确。',
      404: '装载任务不存在或已被移除，请刷新列表。',
      502: 'Java BFF 暂时无法读取 Python 装载任务详情。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/**
 * 上传文本型 Packing List，获取不落库的 RULES_V3 预览。
 *
 * 不要手工设置 Content-Type；浏览器会为 FormData 自动生成 multipart boundary。
 */
export function previewLoadPlanningPackingList(file: File): Promise<PackingListPreview> {
  const body = new FormData()
  body.append('file', file)
  return requestWithOptions<PackingListPreview>(
    '/api/v1/platform/load-planning/packing-list-imports/preview',
    { method: 'POST', body },
    {
      400: '请选择有效的非空 PDF 文件。',
      413: 'PDF 不能超过 5MB。',
      415: '只接受 PDF 文件。',
      422: 'PDF 无法识别、没有文字层，或当前需要 OCR；本版本暂不支持扫描件 OCR。',
      502: 'Java BFF 暂时无法调用 Python 装载规划服务。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/**
 * 启动一次状态化装载规划。
 *
 * 后端会先检查任务约束；资料不足时返回 AWAITING_INPUT 和结构化问题，而不是编造数值。
 */
export function createLoadPlanningAgentRun(
  taskNumber: string,
  input: CreateLoadPlanningAgentRunInput,
  idempotencyKey?: string,
): Promise<LoadPlanningAgentRun> {
  return requestWithOptions<LoadPlanningAgentRun>(
    `/api/v1/platform/load-planning/tasks/${encodeURIComponent(taskNumber)}/agent-runs`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
      },
      body: JSON.stringify(input),
    },
    {
      404: '装载任务不存在，请刷新任务列表。',
      409: '当前任务状态不允许启动新的规划，请刷新后检查。',
      422: '规划目标或托盘选择不符合要求。',
      502: 'Java BFF 暂时无法启动 Python 装载规划智能体。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/** 重新读取智能体权威状态；页面不在浏览器中自行推进步骤。 */
export function getLoadPlanningAgentRun(runId: string): Promise<LoadPlanningAgentRun> {
  return requestWithOptions<LoadPlanningAgentRun>(
    `/api/v1/platform/load-planning/agent-runs/${encodeURIComponent(runId)}`,
    undefined,
    {
      404: '这次规划运行不存在或已被清理。',
      502: 'Java BFF 暂时无法读取装载规划状态。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/** 换设备或清空浏览器缓存后，查询服务端仍可继续的运行。 */
export function getActiveLoadPlanningAgentRuns(
  taskNumber: string,
): Promise<LoadPlanningAgentRun[]> {
  return requestWithOptions<LoadPlanningAgentRun[]>(
    `/api/v1/platform/load-planning/tasks/${encodeURIComponent(taskNumber)}/agent-runs`,
    undefined,
    {
      404: '装载任务不存在，请刷新任务列表。',
      502: 'Java BFF 暂时无法读取可恢复装载规划。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/**
 * 查询任务的完整智能体运行历史。
 *
 * 与 active 接口不同，这里会返回 APPROVED、REJECTED 和 FAILED，供企业任务页判断
 * “已批准待发布”、失败待处理及重规划链；页面不能把 FAILED 当作可直接 resume 的运行。
 */
export function getLoadPlanningAgentRunHistory(
  taskNumber: string,
): Promise<LoadPlanningAgentRun[]> {
  return requestWithOptions<LoadPlanningAgentRun[]>(
    `/api/v1/platform/load-planning/tasks/${encodeURIComponent(taskNumber)}/agent-runs/history`,
    undefined,
    {
      404: '装载任务不存在，请刷新任务列表。',
      502: 'Java BFF 暂时无法读取装载规划历史。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/**
 * 用人工确认的结构化答案恢复流程。
 *
 * value 保留 boolean、number 和 string 的原始 JSON 类型，不能把 false 当成空值丢掉。
 */
export function resumeLoadPlanningAgentRun(
  runId: string,
  input: ResumeLoadPlanningAgentRunInput,
): Promise<LoadPlanningAgentRun> {
  return requestWithOptions<LoadPlanningAgentRun>(
    `/api/v1/platform/load-planning/agent-runs/${encodeURIComponent(runId)}/resume`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    {
      404: '这次规划运行不存在，请重新启动。',
      409: '当前步骤不等待补充信息，请刷新智能体状态。',
      422: '补充的约束格式不正确，请检查单位和范围。',
      502: 'Java BFF 暂时无法恢复 Python 装载规划。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/** 人工审批只选择后端已生成的可行方案；浏览器无权修改评分或坐标。 */
export function decideLoadPlanningAgentRun(
  runId: string,
  input: DecideLoadPlanningAgentRunInput,
): Promise<LoadPlanningAgentRun> {
  return requestWithOptions<LoadPlanningAgentRun>(
    `/api/v1/platform/load-planning/agent-runs/${encodeURIComponent(runId)}/decision`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    {
      404: '规划运行或待审批方案不存在。',
      409: '当前规划状态或方案可行性不允许执行该审批。',
      422: '审批参数不正确。',
      502: 'Java BFF 暂时无法提交规划审批。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/**
 * 从驳回/失败运行创建一条新的运行。
 *
 * 旧 run 不被覆盖，后端通过 parentRunId 保留重规划链；页面只提交原因和
 * 可选的策略/托盘偏好，箱规事实仍必须通过新的任务版本变更。新调用方应生成并传入
 * idempotencyKey；网络超时后复用同一个 key，不能为重试生成新 key。
 */
export function replanLoadPlanningAgentRun(
  runId: string,
  input: ReplanLoadPlanningAgentRunInput,
  idempotencyKey?: string,
): Promise<LoadPlanningAgentRun> {
  return requestWithOptions<LoadPlanningAgentRun>(
    `/api/v1/platform/load-planning/agent-runs/${encodeURIComponent(runId)}/replan`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
      },
      body: JSON.stringify(input),
    },
    {
      404: '原规划运行不存在。',
      409: '当前运行状态不允许重新规划。',
      422: '重新规划原因或策略参数不正确。',
      502: 'Java BFF 暂时无法创建新的装载规划运行。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/**
 * 对运行中已经生成的候选方案做一次辅助解读。
 *
 * 该调用与审批、驳回和发布接口完全独立；即使返回 503，当前确定性方案和人工决策按钮
 * 仍可正常使用。页面不向用户暴露模型密钥、原始提示词或服务内部配置。
 */
export function generateLoadPlanningPlanInsight(
  runId: string,
  planCode: string | null,
): Promise<LoadPlanningPlanInsight> {
  return requestWithOptions<LoadPlanningPlanInsight>(
    `/api/v1/platform/load-planning/agent-runs/${encodeURIComponent(runId)}/plan-insights`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ planCode }),
    },
    {
      404: '这次规划运行不存在，请刷新后重新进入工作台。',
      409: '当前运行还没有可供解读的候选方案。',
      422: '当前选择的方案已经变化，请重新选择后再生成解读。',
      502: '方案解读暂时失败，可稍后重试；装载规划与人工审批不受影响。',
      503: '方案解读能力当前未启用或暂时不可用；装载规划与人工审批仍可正常进行。',
    },
  )
}

/**
 * 查询任务已经正式发布的不可变版本链。
 *
 * 使用列表接口而不是 current 接口，是因为“尚未发布”在工作台里是正常状态，不应被当成
 * 页面错误；空数组就表示当前 expectedCurrentVersionNumber 为 0。
 */
export function getLoadPlanVersions(taskNumber: string): Promise<LoadPlanVersion[]> {
  return requestWithOptions<LoadPlanVersion[]>(
    `/api/v1/platform/load-planning/tasks/${encodeURIComponent(taskNumber)}/plan-versions`,
    undefined,
    {
      404: '装载任务不存在，请刷新任务列表。',
      502: 'Java BFF 暂时无法读取正式装载方案版本。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/**
 * 把已批准候选发布为正式 V1/V2。
 *
 * 浏览器不提交 planCode、审批人或锁定人；Python 只能使用运行中已经批准的方案，并从
 * 服务器身份上下文写入审计身份，避免前端伪造“谁批准、谁发布”。
 */
export function lockLoadPlanVersion(
  runId: string,
  input: LockLoadPlanVersionInput,
): Promise<LoadPlanVersion> {
  return requestWithOptions<LoadPlanVersion>(
    `/api/v1/platform/load-planning/agent-runs/${encodeURIComponent(runId)}/plan-versions`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    {
      403: '当前服务器身份没有正式发布权限。',
      404: '规划运行、任务或待发布方案不存在。',
      409: '运行、任务输入或当前正式版本已经变化，请刷新后重新确认。',
      422: '锁定原因或版本参数不正确。',
      502: 'Java BFF 暂时无法发布正式装载方案。',
      503: 'Nacos 中没有可用的装载规划服务实例。',
    },
  )
}

/** 查询全部平台组织。 */
export function getOrganizations(): Promise<Organization[]> {
  return request<Organization[]>('/api/v1/platform/organizations')
}

/** 创建平台组织；成功时返回 Java BFF 保存后的完整组织数据。 */
export function createOrganization(input: {
  organizationCode: string
  organizationName: string
}): Promise<Organization> {
  return requestWithOptions<Organization>(
    '/api/v1/platform/organizations',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(input),
    },
    {
      409: '组织编码已存在，请更换编码。',
    },
  )
}

/** 查询全部平台用户。 */
export function getPlatformUsers(): Promise<PlatformUser[]> {
  return request<PlatformUser[]>('/api/v1/platform/users')
}

/** 创建平台用户；用户必须选择一个已经存在的组织。 */
export function createPlatformUser(input: {
  username: string
  displayName: string
  organizationId: number
}): Promise<PlatformUser> {
  return requestWithOptions<PlatformUser>(
    '/api/v1/platform/users',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(input),
    },
    {
      404: '所选组织不存在或已被移除，请重新选择。',
      409: '用户名已存在，请更换用户名。',
    },
  )
}

/** 查询平台角色。 */
export function getPlatformRoles(): Promise<PlatformRole[]> {
  return request<PlatformRole[]>('/api/v1/platform/roles')
}

/** 创建平台角色。 */
export function createPlatformRole(input: {
  roleCode: string
  roleName: string
}): Promise<PlatformRole> {
  return requestWithOptions<PlatformRole>(
    '/api/v1/platform/roles',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    { 409: '角色编码已存在，请更换编码。' },
  )
}

/** 查询平台权限。 */
export function getPlatformPermissions(): Promise<PlatformPermission[]> {
  return request<PlatformPermission[]>('/api/v1/platform/permissions')
}

/** 创建平台权限。 */
export function createPlatformPermission(input: {
  permissionCode: string
  permissionName: string
}): Promise<PlatformPermission> {
  return requestWithOptions<PlatformPermission>(
    '/api/v1/platform/permissions',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    { 409: '权限编码已存在，请更换编码。' },
  )
}

/** 查询角色已经拥有的权限。 */
export function getRolePermissions(roleId: number): Promise<PlatformPermission[]> {
  return request<PlatformPermission[]>(`/api/v1/platform/roles/${roleId}/permissions`)
}

/** 给角色增加一个权限。 */
export function assignPermissionToRole(
  roleId: number,
  permissionId: number,
): Promise<PlatformPermission[]> {
  return requestWithOptions<PlatformPermission[]>(
    `/api/v1/platform/roles/${roleId}/permissions/${permissionId}`,
    { method: 'POST' },
    { 404: '角色或权限不存在，请刷新后重试。' },
  )
}

/** 查询用户已经拥有的角色。 */
export function getUserRoles(userId: number): Promise<PlatformRole[]> {
  return request<PlatformRole[]>(`/api/v1/platform/users/${userId}/roles`)
}

/** 给用户增加一个角色。 */
export function assignRoleToUser(userId: number, roleId: number): Promise<PlatformRole[]> {
  return requestWithOptions<PlatformRole[]>(
    `/api/v1/platform/users/${userId}/roles/${roleId}`,
    { method: 'POST' },
    { 404: '用户或角色不存在，请刷新后重试。' },
  )
}

/** 查询 Java BFF 组装后的平台菜单树。 */
export function getPlatformMenuTree(): Promise<PlatformMenu[]> {
  return request<PlatformMenu[]>('/api/v1/platform/menus/tree')
}
