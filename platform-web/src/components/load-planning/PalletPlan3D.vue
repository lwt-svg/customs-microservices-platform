<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

import type {
  LoadPlanningPalletSpec,
  LoadPlanningPlacement,
  LoadPlanningPlanPallet,
} from '@/api/platform'

type CameraMode = 'perspective' | 'orthographic'
type ViewPreset = 'isometric' | 'top' | 'front' | 'side'
type ActiveView = ViewPreset | 'custom'

interface LayerOption {
  index: number
  zMm: number
  count: number
  inferred: boolean
}

const props = defineProps<{
  pallet: LoadPlanningPlanPallet | null
  palletSpec: LoadPlanningPalletSpec | null
}>()

const panel = ref<HTMLElement | null>(null)
const viewport = ref<HTMLDivElement | null>(null)
const renderError = ref('')
const selectedPlacement = ref<LoadPlanningPlacement | null>(null)
const selectedLayer = ref<'all' | number>('all')
const hiddenItemCodes = ref<Set<string>>(new Set())
const explodePercent = ref(0)
const cameraMode = ref<CameraMode>('perspective')
const activeView = ref<ActiveView>('isometric')
const isFullscreen = ref(false)

let scene: THREE.Scene | null = null
let perspectiveCamera: THREE.PerspectiveCamera | null = null
let orthographicCamera: THREE.OrthographicCamera | null = null
let activeCamera: THREE.PerspectiveCamera | THREE.OrthographicCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let contentGroup: THREE.Group | null = null
let resizeObserver: ResizeObserver | null = null
let renderFrame = 0
let selectedMesh: THREE.Mesh | null = null
let boxMeshes: THREE.Mesh[] = []
let keyLight: THREE.DirectionalLight | null = null
let sceneSpan = 1
let orthographicViewHeight = 2.4
let displayHeightWorld = 1
let pointerStart: { x: number; y: number } | null = null

/**
 * 后端坐标使用 mm；Three.js 场景统一缩放成“米级”世界坐标，避免相机裁剪面跨度过大。
 *
 * 坐标映射：
 * - 后端 x → Three.js X；
 * - 后端 y → Three.js Z（托盘纵深）；
 * - 后端 z → Three.js Y（竖直方向）。
 *
 * 页面绝不重新计算装载坐标。分层展开只改变箱体的临时显示偏移，
 * Inspector 始终展示后端返回的原始权威坐标。
 */
const MM_TO_WORLD = 0.001

const viewPresets: Array<{ value: ViewPreset; label: string; shortcut: string }> = [
  { value: 'isometric', label: '等轴', shortcut: '1' },
  { value: 'top', label: '俯视', shortcut: '2' },
  { value: 'front', label: '正视', shortcut: '3' },
  { value: 'side', label: '侧视', shortcut: '4' },
]

function resolvedLayerIndex(
  placement: LoadPlanningPlacement,
  placements: LoadPlanningPlacement[] = props.pallet?.placements ?? [],
): number {
  if (placement.layer != null && placement.layer >= 1) return placement.layer
  const fallbackZValues = [...new Set(placements.map((entry) => entry.zMm))].sort(
    (a, b) => a - b,
  )
  return fallbackZValues.indexOf(placement.zMm) + 1
}

const layerOptions = computed<LayerOption[]>(() => {
  const placements = props.pallet?.placements ?? []
  const grouped = new Map<number, LayerOption>()
  for (const placement of placements) {
    const index = resolvedLayerIndex(placement, placements)
    const current = grouped.get(index)
    if (current) {
      current.count += 1
      current.zMm = Math.min(current.zMm, placement.zMm)
      current.inferred = current.inferred || placement.layer == null
    } else {
      grouped.set(index, {
        index,
        zMm: placement.zMm,
        count: 1,
        inferred: placement.layer == null,
      })
    }
  }
  return [...grouped.values()].sort((a, b) => a.index - b.index)
})

const usesInferredLayers = computed(() =>
  (props.pallet?.placements ?? []).some((placement) => placement.layer == null),
)

const legend = computed(() => {
  const placements = props.pallet?.placements ?? []
  const codes = [...new Set(placements.map((placement) => placement.itemCode))]
  return codes.map((itemCode) => ({
    itemCode,
    color: colorForItem(itemCode),
    count: placements.filter((placement) => placement.itemCode === itemCode).length,
    visible: !hiddenItemCodes.value.has(itemCode),
  }))
})

const visiblePlacements = computed(() => {
  const placements = props.pallet?.placements ?? []
  const selectedLayerOption =
    selectedLayer.value === 'all'
      ? null
      : layerOptions.value.find((layer) => layer.index === selectedLayer.value)
  return placements.filter(
    (placement) =>
      !hiddenItemCodes.value.has(placement.itemCode) &&
      (!selectedLayerOption ||
        resolvedLayerIndex(placement, placements) === selectedLayerOption.index),
  )
})

const visibleBoxCount = computed(() => visiblePlacements.value.length)

const maximumHeightMm = computed(
  () =>
    props.palletSpec?.maxTotalHeightMm ??
    Math.max(props.pallet?.loadedHeightMm ?? 0, props.palletSpec?.heightMm ?? 0),
)

const exceedsHeightLimit = computed(
  () =>
    props.palletSpec?.maxTotalHeightMm != null &&
    (props.pallet?.loadedHeightMm ?? 0) > props.palletSpec.maxTotalHeightMm,
)

const viewportAriaLabel = computed(() => {
  if (!props.pallet || !props.palletSpec) return '托盘三维复核区域，当前没有可展示的托盘'
  return `托盘 ${props.pallet.palletIndex} 三维复核，共 ${props.pallet.boxCount} 箱，当前显示 ${visibleBoxCount.value} 箱。可拖动旋转、滚轮缩放，按数字 1 到 4 切换视角。`
})

function formatRatio(value: number | null): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function formatPatternType(value: string | null): string {
  if (value === 'GRID') return '规则网格'
  if (value === 'STRIP_MIXED') return '混合条带'
  return value ?? ''
}

function colorForItem(itemCode: string): string {
  let hash = 0
  for (const char of itemCode) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  const hue = 145 + (hash % 185)
  // Three.js r186 的 Color.setStyle 仍按带逗号的 HSL 语法解析；CSS 图例同样兼容。
  // 如果使用现代 CSS 的空格语法，HTML 图例有颜色，但 WebGL 箱体会回退成白色。
  return `hsl(${hue}, 48%, 52%)`
}

function placementKey(placement: LoadPlanningPlacement): string {
  return placement.placementId ?? `${placement.itemCode}-${placement.boxIndex}`
}

const selectedPlacementKey = computed({
  get: () => (selectedPlacement.value ? placementKey(selectedPlacement.value) : ''),
  set: (value: string) => {
    const mesh = boxMeshes.find((candidate) => {
      const placement = candidate.userData.placement as LoadPlanningPlacement
      return candidate.visible && placementKey(placement) === value
    })
    selectMesh(mesh ?? null)
  },
})

function disposeObject(object: THREE.Object3D): void {
  object.traverse((child) => {
    if (
      child instanceof THREE.Mesh ||
      child instanceof THREE.Line ||
      child instanceof THREE.LineSegments
    ) {
      child.geometry?.dispose()
      const material = child.material
      if (Array.isArray(material)) {
        material.forEach((entry) => entry.dispose())
      } else {
        material?.dispose()
      }
    }
  })
}

function requestRender(): void {
  if (renderFrame || document.hidden) return
  renderFrame = window.requestAnimationFrame(() => {
    renderFrame = 0
    if (scene && activeCamera && renderer) renderer.render(scene, activeCamera)
  })
}

function handleControlsStart(): void {
  activeView.value = 'custom'
}

function disposeControls(): void {
  if (!controls) return
  controls.removeEventListener('change', requestRender)
  controls.removeEventListener('start', handleControlsStart)
  controls.dispose()
  controls = null
}

function setupControls(camera: THREE.Camera, target: THREE.Vector3): void {
  if (!renderer) return
  disposeControls()
  controls = new OrbitControls(camera, renderer.domElement)
  // 不使用 damping，OrbitControls 发生变化时只渲染一帧，静止后不占用 GPU。
  controls.enableDamping = false
  controls.screenSpacePanning = true
  controls.minDistance = sceneSpan * 0.35
  controls.maxDistance = sceneSpan * 8
  controls.minZoom = 0.35
  controls.maxZoom = 6
  controls.target.copy(target)
  controls.addEventListener('change', requestRender)
  controls.addEventListener('start', handleControlsStart)
  controls.update()
}

function clearContent(): void {
  if (contentGroup) {
    for (const child of [...contentGroup.children]) {
      contentGroup.remove(child)
      disposeObject(child)
    }
  }
  boxMeshes = []
  selectedMesh = null
  selectedPlacement.value = null
}

function addWoodPiece(
  group: THREE.Group,
  size: THREE.Vector3,
  position: THREE.Vector3,
  material: THREE.MeshStandardMaterial,
): void {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), material)
  mesh.position.copy(position)
  mesh.castShadow = true
  mesh.receiveShadow = true
  group.add(mesh)

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(mesh.geometry),
    new THREE.LineBasicMaterial({
      color: 0x6b4930,
      transparent: true,
      opacity: 0.42,
    }),
  )
  mesh.add(edges)
}

function addPalletModel(length: number, width: number, height: number): void {
  if (!contentGroup) return

  const palletGroup = new THREE.Group()
  palletGroup.name = 'pallet-model'
  const woodMaterial = new THREE.MeshStandardMaterial({
    color: 0xb98252,
    roughness: 0.82,
    metalness: 0,
  })
  const topThickness = height * 0.24
  const bottomThickness = height * 0.16
  const supportHeight = Math.max(height - topThickness - bottomThickness, height * 0.35)
  const slatCount = 7
  const slatWidth = length / (slatCount * 1.62)
  const slatTravel = Math.max(length - slatWidth - length * 0.06, 0)

  for (let index = 0; index < slatCount; index += 1) {
    const ratio = index / (slatCount - 1)
    addWoodPiece(
      palletGroup,
      new THREE.Vector3(slatWidth, topThickness, width * 0.96),
      new THREE.Vector3(
        -slatTravel / 2 + slatTravel * ratio,
        height - topThickness / 2,
        0,
      ),
      woodMaterial,
    )
  }

  for (const zRatio of [-0.36, 0, 0.36]) {
    addWoodPiece(
      palletGroup,
      new THREE.Vector3(length * 0.96, bottomThickness, width * 0.14),
      new THREE.Vector3(0, bottomThickness / 2, width * zRatio),
      woodMaterial,
    )
  }

  for (const xRatio of [-0.38, 0, 0.38]) {
    for (const zRatio of [-0.35, 0, 0.35]) {
      addWoodPiece(
        palletGroup,
        new THREE.Vector3(length * 0.12, supportHeight, width * 0.14),
        new THREE.Vector3(
          length * xRatio,
          bottomThickness + supportHeight / 2,
          width * zRatio,
        ),
        woodMaterial,
      )
    }
  }

  contentGroup.add(palletGroup)
}

function addBoundaryHelpers(
  palletLength: number,
  palletWidth: number,
  boundaryHeight: number,
): void {
  if (!contentGroup) return

  const helperGroup = new THREE.Group()
  helperGroup.name = 'dimension-boundary'
  const boundaryColor = exceedsHeightLimit.value ? 0xd7655b : 0x62947c
  const boundaryGeometry = new THREE.BoxGeometry(palletLength, boundaryHeight, palletWidth)
  const boundary = new THREE.LineSegments(
    new THREE.EdgesGeometry(boundaryGeometry),
    new THREE.LineBasicMaterial({
      color: boundaryColor,
      transparent: true,
      opacity: 0.5,
    }),
  )
  boundary.position.y = boundaryHeight / 2
  helperGroup.add(boundary)
  boundaryGeometry.dispose()

  const topPlane = new THREE.Mesh(
    new THREE.PlaneGeometry(palletLength, palletWidth),
    new THREE.MeshBasicMaterial({
      color: boundaryColor,
      transparent: true,
      opacity: 0.045,
      depthWrite: false,
      side: THREE.DoubleSide,
    }),
  )
  topPlane.rotation.x = -Math.PI / 2
  topPlane.position.y = boundaryHeight
  helperGroup.add(topPlane)

  const markerOffset = Math.max(palletLength, palletWidth) * 0.075
  const markerX = -palletLength / 2 - markerOffset
  const markerZ = palletWidth / 2 + markerOffset
  const tick = markerOffset * 0.42
  const markerGeometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(markerX, 0, markerZ),
    new THREE.Vector3(markerX, boundaryHeight, markerZ),
    new THREE.Vector3(markerX - tick, 0, markerZ),
    new THREE.Vector3(markerX + tick, 0, markerZ),
    new THREE.Vector3(markerX - tick, boundaryHeight, markerZ),
    new THREE.Vector3(markerX + tick, boundaryHeight, markerZ),
  ])
  const marker = new THREE.LineSegments(
    markerGeometry,
    new THREE.LineBasicMaterial({ color: boundaryColor, transparent: true, opacity: 0.9 }),
  )
  helperGroup.add(marker)
  contentGroup.add(helperGroup)
}

function addBox(
  placement: LoadPlanningPlacement,
  palletLengthMm: number,
  palletWidthMm: number,
  layerIndex: number,
): void {
  if (!contentGroup) return

  const length = placement.lengthMm * MM_TO_WORLD
  const width = placement.widthMm * MM_TO_WORLD
  const height = placement.heightMm * MM_TO_WORLD
  const geometry = new THREE.BoxGeometry(length, height, width)
  const material = new THREE.MeshStandardMaterial({
    color: colorForItem(placement.itemCode),
    roughness: 0.66,
    metalness: 0.015,
  })
  const mesh = new THREE.Mesh(geometry, material)

  /**
   * 后端给出的是箱体左下后角坐标；Three.js BoxGeometry 的 position 表示几何中心。
   *
   * 关键：zMm 的原点是空托盘底面。首层 zMm 已经等于空托高度，不能再加
   * palletSpec.heightMm，否则 3D 视图会把每层箱子错误抬高一个托盘高度。
   */
  mesh.position.set(
    (placement.xMm + placement.lengthMm / 2 - palletLengthMm / 2) * MM_TO_WORLD,
    (placement.zMm + placement.heightMm / 2) * MM_TO_WORLD,
    (placement.yMm + placement.widthMm / 2 - palletWidthMm / 2) * MM_TO_WORLD,
  )
  mesh.userData.placement = placement
  mesh.userData.layerIndex = layerIndex
  mesh.userData.baseY = mesh.position.y
  mesh.castShadow = true
  mesh.receiveShadow = true
  mesh.name = `${placement.itemCode}-${placement.boxIndex}`

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(geometry),
    new THREE.LineBasicMaterial({
      color: 0x163b32,
      transparent: true,
      opacity: 0.5,
    }),
  )
  mesh.add(edges)

  if (placement.rotationDeg != null) {
    const rotated = Math.abs(placement.rotationDeg % 180) === 90
    const directionMarker = new THREE.Mesh(
      new THREE.BoxGeometry(
        rotated ? length * 0.055 : length * 0.48,
        Math.max(height * 0.006, 0.0015),
        rotated ? width * 0.48 : width * 0.055,
      ),
      new THREE.MeshBasicMaterial({
        color: 0xf4f0dc,
        transparent: true,
        opacity: 0.82,
      }),
    )
    directionMarker.position.y = height / 2 + Math.max(height * 0.005, 0.001)
    directionMarker.userData.visualOnly = true
    mesh.add(directionMarker)
  }

  contentGroup.add(mesh)
  boxMeshes.push(mesh)
}

function updateMeshPresentation(): void {
  const maxBoxHeight =
    Math.max(...(props.pallet?.placements.map((placement) => placement.heightMm) ?? [0]), 1) *
    MM_TO_WORLD
  const explosionGap = maxBoxHeight * 0.36 * (explodePercent.value / 100)

  for (const mesh of boxMeshes) {
    const placement = mesh.userData.placement as LoadPlanningPlacement
    const layerIndex = mesh.userData.layerIndex as number
    const matchesLayer = selectedLayer.value === 'all' || selectedLayer.value === layerIndex
    mesh.visible = matchesLayer && !hiddenItemCodes.value.has(placement.itemCode)
    mesh.position.y = (mesh.userData.baseY as number) + (layerIndex - 1) * explosionGap
  }

  if (selectedMesh && !selectedMesh.visible) selectMesh(null)
  requestRender()
}

function updateCameraProjection(): void {
  if (!viewport.value) return
  const width = Math.max(viewport.value.clientWidth, 1)
  const height = Math.max(viewport.value.clientHeight, 1)
  const aspect = width / height

  if (perspectiveCamera) {
    perspectiveCamera.aspect = aspect
    perspectiveCamera.near = Math.max(sceneSpan / 200, 0.001)
    perspectiveCamera.far = sceneSpan * 40
    perspectiveCamera.updateProjectionMatrix()
  }

  if (orthographicCamera) {
    const halfHeight = orthographicViewHeight / 2
    orthographicCamera.left = -halfHeight * aspect
    orthographicCamera.right = halfHeight * aspect
    orthographicCamera.top = halfHeight
    orthographicCamera.bottom = -halfHeight
    orthographicCamera.near = Math.max(sceneSpan / 200, 0.001)
    orthographicCamera.far = sceneSpan * 40
    orthographicCamera.updateProjectionMatrix()
  }
}

function cameraTarget(): THREE.Vector3 {
  return new THREE.Vector3(0, displayHeightWorld * 0.44, 0)
}

function setView(preset: ViewPreset): void {
  if (!activeCamera || !controls) return
  const target = cameraTarget()
  const distance = sceneSpan * 2.25
  activeCamera.up.set(0, 1, 0)

  if (preset === 'isometric') {
    activeCamera.position.copy(target).add(new THREE.Vector3(distance * 0.7, distance * 0.56, distance * 0.74))
  } else if (preset === 'top') {
    activeCamera.up.set(0, 0, -1)
    activeCamera.position.copy(target).add(new THREE.Vector3(0, distance, 0.0001))
  } else if (preset === 'front') {
    activeCamera.position.copy(target).add(new THREE.Vector3(0, 0, distance))
  } else {
    activeCamera.position.copy(target).add(new THREE.Vector3(distance, 0, 0))
  }

  if (activeCamera instanceof THREE.OrthographicCamera) {
    activeCamera.zoom = 1
    activeCamera.updateProjectionMatrix()
  }
  controls.target.copy(target)
  controls.update()
  activeView.value = preset
  requestRender()
}

function resetView(): void {
  setView('isometric')
}

function setCameraMode(mode: CameraMode): void {
  if (
    cameraMode.value === mode ||
    !perspectiveCamera ||
    !orthographicCamera ||
    !activeCamera
  ) {
    return
  }

  const position = activeCamera.position.clone()
  const up = activeCamera.up.clone()
  const target = controls?.target.clone() ?? cameraTarget()
  // 切换“透视 / 正交”只改变投影方式，不应该把已经选择的俯视、正视等方向
  // 误判成用户手动拖动后的自定义视角。否则画面方向虽然没变，工具栏高亮却会消失。
  const previousView = activeView.value
  cameraMode.value = mode
  activeCamera = mode === 'perspective' ? perspectiveCamera : orthographicCamera
  activeCamera.position.copy(position)
  activeCamera.up.copy(up)
  updateCameraProjection()
  setupControls(activeCamera, target)
  activeView.value = previousView
  requestRender()
}

function resizeRenderer(): void {
  if (!viewport.value || !renderer) return
  const width = Math.max(viewport.value.clientWidth, 1)
  const height = Math.max(viewport.value.clientHeight, 1)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(width, height, false)
  updateCameraProjection()
  requestRender()
}

function rebuildScene(): void {
  if (!scene || !activeCamera || !controls || !props.pallet || !props.palletSpec) {
    clearContent()
    requestRender()
    return
  }

  clearContent()
  const spec = props.palletSpec
  const palletLength = spec.lengthMm * MM_TO_WORLD
  const palletWidth = spec.widthMm * MM_TO_WORLD
  const palletHeight = spec.heightMm * MM_TO_WORLD
  const boundaryHeight = Math.max(maximumHeightMm.value, spec.heightMm) * MM_TO_WORLD
  displayHeightWorld = Math.max(props.pallet.loadedHeightMm, spec.heightMm) * MM_TO_WORLD
  sceneSpan = Math.max(palletLength, palletWidth, boundaryHeight, displayHeightWorld, 0.8)
  orthographicViewHeight = sceneSpan * 2.25

  addPalletModel(palletLength, palletWidth, palletHeight)
  const layerIndexByZ = new Map(layerOptions.value.map((layer) => [layer.zMm, layer.index]))
  for (const placement of props.pallet.placements) {
    addBox(
      placement,
      spec.lengthMm,
      spec.widthMm,
      placement.layer ?? layerIndexByZ.get(placement.zMm) ?? 1,
    )
  }
  addBoundaryHelpers(palletLength, palletWidth, boundaryHeight)

  if (
    selectedLayer.value !== 'all' &&
    !layerOptions.value.some((layer) => layer.index === selectedLayer.value)
  ) {
    selectedLayer.value = 'all'
  }
  const availableCodes = new Set(legend.value.map((entry) => entry.itemCode))
  hiddenItemCodes.value = new Set(
    [...hiddenItemCodes.value].filter((itemCode) => availableCodes.has(itemCode)),
  )

  if (scene.fog instanceof THREE.Fog) {
    scene.fog.near = sceneSpan * 2.8
    scene.fog.far = sceneSpan * 10
  }
  if (keyLight) {
    keyLight.position.set(sceneSpan * 2.5, sceneSpan * 4, sceneSpan * 3)
    const shadowCamera = keyLight.shadow.camera
    shadowCamera.left = -sceneSpan * 2
    shadowCamera.right = sceneSpan * 2
    shadowCamera.top = sceneSpan * 3
    shadowCamera.bottom = -sceneSpan
    shadowCamera.far = sceneSpan * 12
    shadowCamera.updateProjectionMatrix()
  }

  updateCameraProjection()
  setupControls(activeCamera, cameraTarget())
  updateMeshPresentation()
  setView('isometric')
}

function selectMesh(mesh: THREE.Mesh | null): void {
  if (selectedMesh) {
    const previous = selectedMesh.material
    if (previous instanceof THREE.MeshStandardMaterial) {
      previous.emissive.setHex(0x000000)
      previous.emissiveIntensity = 1
    }
  }
  selectedMesh = mesh
  selectedPlacement.value =
    (mesh?.userData.placement as LoadPlanningPlacement | undefined) ?? null
  if (mesh) {
    const material = mesh.material
    if (material instanceof THREE.MeshStandardMaterial) {
      material.emissive.setHex(0x2e8062)
      material.emissiveIntensity = 0.45
    }
  }
  requestRender()
}

function handlePointerDown(event: PointerEvent): void {
  pointerStart = { x: event.clientX, y: event.clientY }
}

function handlePointerUp(event: PointerEvent): void {
  if (!renderer || !activeCamera || !pointerStart || event.button !== 0) return
  const movement = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y)
  pointerStart = null
  if (movement > 5) return

  const bounds = renderer.domElement.getBoundingClientRect()
  const pointer = new THREE.Vector2(
    ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
  )
  const raycaster = new THREE.Raycaster()
  raycaster.setFromCamera(pointer, activeCamera)
  const hit = raycaster.intersectObjects(
    boxMeshes.filter((mesh) => mesh.visible),
    false,
  )[0]
  selectMesh(hit?.object instanceof THREE.Mesh ? hit.object : null)
}

function toggleItemVisibility(itemCode: string): void {
  const next = new Set(hiddenItemCodes.value)
  if (next.has(itemCode)) next.delete(itemCode)
  else next.add(itemCode)
  hiddenItemCodes.value = next
}

function showAllItems(): void {
  hiddenItemCodes.value = new Set()
}

function zoomCamera(factor: number): void {
  if (!activeCamera || !controls) return
  if (activeCamera instanceof THREE.PerspectiveCamera) {
    const offset = activeCamera.position.clone().sub(controls.target).multiplyScalar(factor)
    activeCamera.position.copy(controls.target).add(offset)
  } else {
    activeCamera.zoom = THREE.MathUtils.clamp(activeCamera.zoom / factor, 0.35, 6)
    activeCamera.updateProjectionMatrix()
  }
  controls.update()
  activeView.value = 'custom'
  requestRender()
}

function handleViewportKeydown(event: KeyboardEvent): void {
  const keyActions: Record<string, () => void> = {
    '1': () => setView('isometric'),
    '2': () => setView('top'),
    '3': () => setView('front'),
    '4': () => setView('side'),
    Home: resetView,
    '0': resetView,
    '+': () => zoomCamera(0.84),
    '=': () => zoomCamera(0.84),
    '-': () => zoomCamera(1.18),
  }
  const action = keyActions[event.key]
  if (!action) return
  event.preventDefault()
  action()
}

async function toggleFullscreen(): Promise<void> {
  if (!panel.value) return
  try {
    if (document.fullscreenElement === panel.value) {
      await document.exitFullscreen()
    } else {
      await panel.value.requestFullscreen()
    }
  } catch (error) {
    renderError.value =
      error instanceof Error ? `无法切换全屏：${error.message}` : '当前浏览器不支持全屏复核。'
  }
}

function handleFullscreenChange(): void {
  isFullscreen.value = document.fullscreenElement === panel.value
  void nextTick(resizeRenderer)
}

function handleVisibilityChange(): void {
  if (!document.hidden) requestRender()
}

onMounted(async () => {
  await nextTick()
  if (!viewport.value) return

  try {
    scene = new THREE.Scene()
    scene.background = new THREE.Color(0xeef3f0)
    scene.fog = new THREE.Fog(0xeef3f0, 3, 12)
    perspectiveCamera = new THREE.PerspectiveCamera(38, 1, 0.01, 100)
    orthographicCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 100)
    activeCamera = perspectiveCamera

    renderer = new THREE.WebGLRenderer({
      antialias: true,
      powerPreference: 'high-performance',
    })
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.04
    renderer.shadowMap.enabled = true
    // Three.js r186 已移除 PCFSoftShadowMap。PCFShadowMap 保留复核所需层次且无兼容警告。
    renderer.shadowMap.type = THREE.PCFShadowMap
    renderer.domElement.setAttribute('aria-hidden', 'true')
    renderer.domElement.addEventListener('pointerdown', handlePointerDown)
    renderer.domElement.addEventListener('pointerup', handlePointerUp)
    viewport.value.appendChild(renderer.domElement)

    contentGroup = new THREE.Group()
    scene.add(contentGroup)

    scene.add(new THREE.HemisphereLight(0xffffff, 0x52675d, 1.8))
    keyLight = new THREE.DirectionalLight(0xffffff, 2.4)
    keyLight.position.set(4, 7, 5)
    keyLight.castShadow = true
    keyLight.shadow.mapSize.set(1024, 1024)
    keyLight.shadow.bias = -0.00025
    scene.add(keyLight)

    const fillLight = new THREE.DirectionalLight(0xd9f3e5, 0.7)
    fillLight.position.set(-4, 3, -5)
    scene.add(fillLight)

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(14, 14),
      new THREE.MeshStandardMaterial({ color: 0xe5ece8, roughness: 1 }),
    )
    floor.rotation.x = -Math.PI / 2
    floor.position.y = -0.002
    floor.receiveShadow = true
    scene.add(floor)

    const grid = new THREE.GridHelper(14, 56, 0xa9bbb2, 0xd5dfda)
    grid.position.y = 0.001
    const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material]
    gridMaterials.forEach((material) => {
      material.transparent = true
      material.opacity = 0.54
    })
    scene.add(grid)

    setupControls(activeCamera, cameraTarget())
    resizeObserver = new ResizeObserver(resizeRenderer)
    resizeObserver.observe(viewport.value)
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    resizeRenderer()
    rebuildScene()
  } catch (error) {
    renderError.value =
      error instanceof Error ? `3D 初始化失败：${error.message}` : '当前浏览器无法初始化 3D。'
  }
})

watch(
  () => [props.pallet, props.palletSpec],
  () => rebuildScene(),
  { deep: true },
)

watch(
  () => props.pallet,
  () => {
    selectedLayer.value = 'all'
    hiddenItemCodes.value = new Set()
    explodePercent.value = 0
  },
)

watch(
  () => [selectedLayer.value, explodePercent.value, hiddenItemCodes.value] as const,
  () => updateMeshPresentation(),
)

onBeforeUnmount(() => {
  window.cancelAnimationFrame(renderFrame)
  resizeObserver?.disconnect()
  document.removeEventListener('fullscreenchange', handleFullscreenChange)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  disposeControls()
  if (renderer) {
    renderer.domElement.removeEventListener('pointerdown', handlePointerDown)
    renderer.domElement.removeEventListener('pointerup', handlePointerUp)
  }
  if (scene) {
    disposeObject(scene)
    scene.clear()
  }
  if (renderer) {
    renderer.dispose()
    renderer.forceContextLoss()
    renderer.domElement.remove()
  }
  scene = null
  perspectiveCamera = null
  orthographicCamera = null
  activeCamera = null
  renderer = null
  contentGroup = null
  keyLight = null
  boxMeshes = []
})
</script>

<template>
  <section
    ref="panel"
    class="pallet-3d-panel"
    :class="{ 'is-fullscreen': isFullscreen }"
  >
    <header class="pallet-3d-header">
      <div class="pallet-3d-title">
        <span class="pallet-3d-kicker">LOAD PLAN REVIEW</span>
        <strong>托盘装载三维复核</strong>
        <span>后端权威箱体坐标 · 托盘结构外观为示意</span>
      </div>
      <div class="pallet-3d-header-badges" aria-label="视图状态">
        <span>{{ pallet?.boxCount ?? 0 }} 箱</span>
        <span>
          {{ pallet?.layerCount ?? layerOptions.length }} 层{{
            usesInferredLayers ? '（按 Z 推断）' : ''
          }}
        </span>
        <span v-if="pallet?.patternType">{{ formatPatternType(pallet.patternType) }}</span>
        <span class="unit-badge">mm</span>
      </div>
    </header>

    <div v-if="pallet && palletSpec" class="pallet-3d-toolbar">
      <div class="toolbar-group" role="group" aria-label="相机视角">
        <span class="toolbar-label">视角</span>
        <button
          v-for="preset in viewPresets"
          :key="preset.value"
          type="button"
          class="toolbar-button"
          :class="{ active: activeView === preset.value }"
          :aria-pressed="activeView === preset.value"
          :title="`快捷键 ${preset.shortcut}`"
          @click="setView(preset.value)"
        >
          {{ preset.label }}
        </button>
        <button
          type="button"
          class="toolbar-button reset"
          title="快捷键 Home 或 0"
          aria-label="复位到默认等轴视角"
          @click="resetView"
        >
          复位
        </button>
      </div>

      <div class="toolbar-spacer"></div>

      <div class="toolbar-group" role="group" aria-label="投影方式">
        <span class="toolbar-label">投影</span>
        <button
          type="button"
          class="toolbar-button"
          :class="{ active: cameraMode === 'perspective' }"
          :aria-pressed="cameraMode === 'perspective'"
          @click="setCameraMode('perspective')"
        >
          透视
        </button>
        <button
          type="button"
          class="toolbar-button"
          :class="{ active: cameraMode === 'orthographic' }"
          :aria-pressed="cameraMode === 'orthographic'"
          @click="setCameraMode('orthographic')"
        >
          正交
        </button>
      </div>

      <button
        type="button"
        class="fullscreen-button"
        :aria-label="isFullscreen ? '退出全屏复核' : '进入全屏复核'"
        :aria-pressed="isFullscreen"
        @click="toggleFullscreen"
      >
        <span aria-hidden="true">{{ isFullscreen ? '↙' : '↗' }}</span>
        {{ isFullscreen ? '退出全屏' : '全屏复核' }}
      </button>
    </div>

    <!--
      viewport 必须常驻 DOM。方案从“无托盘”切换到“有托盘”时，若用 v-if 销毁它，
      WebGLRenderer 仍会绑定旧节点，后续即使数据有效也只能看到空白区域。
    -->
    <div class="pallet-3d-stage">
      <div
        ref="viewport"
        class="pallet-3d-viewport"
        role="region"
        tabindex="0"
        :aria-label="viewportAriaLabel"
        @keydown="handleViewportKeydown"
        @dblclick="resetView"
      ></div>

      <div v-if="renderError" class="pallet-3d-error" role="alert">
        {{ renderError }}
      </div>

      <div v-if="!pallet || !palletSpec" class="pallet-3d-empty">
        <span aria-hidden="true">3D</span>
        <strong>请选择包含已放置箱子的托盘</strong>
        <p>三维视图只展示后端算法返回的坐标，不会在浏览器中重新排箱。</p>
      </div>

      <template v-else>
      <section class="dimension-card" aria-label="装载尺寸">
        <div class="overlay-heading">
          <span>空间边界</span>
          <i :class="{ warning: exceedsHeightLimit }"></i>
        </div>
        <dl>
          <div>
            <dt>托盘底面</dt>
            <dd>{{ palletSpec.lengthMm }} × {{ palletSpec.widthMm }} mm</dd>
          </div>
          <div>
            <dt>当前成托高度</dt>
            <dd :class="{ danger: exceedsHeightLimit }">
              {{ pallet.loadedHeightMm.toFixed(0) }} mm
            </dd>
          </div>
          <div>
            <dt>{{ palletSpec.maxTotalHeightMm ? '最大限高' : '当前边界高度' }}</dt>
            <dd>{{ maximumHeightMm.toFixed(0) }} mm</dd>
          </div>
          <div>
            <dt>载荷 / 含托总重</dt>
            <dd>
              {{ pallet.payloadWeightKg?.toFixed(1) ?? '—' }} /
              {{ pallet.grossWeightKg.toFixed(1) }} kg
            </dd>
          </div>
        </dl>
        <div class="plan-facts" aria-label="后端规划指标">
          <div>
            <span>层面积利用率</span>
            <strong>{{ formatRatio(pallet.layerAreaUtilization) }}</strong>
          </div>
          <div>
            <span>高度利用率</span>
            <strong>{{ formatRatio(pallet.heightUtilization) }}</strong>
          </div>
          <div>
            <span>归一化中心偏移</span>
            <strong>{{ formatRatio(pallet.balanceOffset) }}</strong>
          </div>
        </div>
        <span class="coordinate-note">
          X 长度 · Y 纵深 · Z 高度；木托板条仅为外观示意，不代表真实结构主数据
        </span>
      </section>

      <section class="pallet-3d-legend" aria-label="货号图例与筛选">
        <div class="overlay-heading">
          <span>货号图例</span>
          <button
            type="button"
            :disabled="hiddenItemCodes.size === 0"
            @click="showAllItems"
          >
            全部显示
          </button>
        </div>
        <div class="legend-items">
          <button
            v-for="entry in legend"
            :key="entry.itemCode"
            type="button"
            :class="{ muted: !entry.visible }"
            :aria-pressed="entry.visible"
            :aria-label="`${entry.visible ? '隐藏' : '显示'}货号 ${entry.itemCode}，${entry.count} 箱`"
            @click="toggleItemVisibility(entry.itemCode)"
          >
            <i :style="{ background: entry.color }"></i>
            <span>{{ entry.itemCode }}</span>
            <em>{{ entry.count }}</em>
          </button>
        </div>
      </section>

      <section class="layer-controller" aria-label="分层查看">
        <div class="overlay-heading">
          <span>分层检查</span>
          <strong>{{ visibleBoxCount }} / {{ pallet.boxCount }} 箱</strong>
        </div>
        <label>
          <span>显示层</span>
          <select v-model="selectedLayer">
            <option value="all">全部层</option>
            <option
              v-for="layer in layerOptions"
              :key="layer.index"
              :value="layer.index"
            >
              {{ layer.inferred ? '推断第' : '第' }} {{ layer.index }} 层 ·
              {{ layer.count }} 箱 · Z={{ layer.zMm }} mm
            </option>
          </select>
        </label>
        <label>
          <span>键盘选箱</span>
          <select v-model="selectedPlacementKey" :disabled="visiblePlacements.length === 0">
            <option value="">未选择箱体</option>
            <option
              v-for="placement in visiblePlacements"
              :key="placementKey(placement)"
              :value="placementKey(placement)"
            >
              {{ placement.itemCode }} · #{{ placement.boxIndex }} ·
              {{ placement.layer == null ? '推断层' : `第 ${placement.layer} 层` }}
            </option>
          </select>
        </label>
        <label class="explode-control">
          <span>
            分层展开
            <output>{{ explodePercent }}%</output>
          </span>
          <input
            v-model.number="explodePercent"
            type="range"
            min="0"
            max="100"
            step="10"
            :disabled="layerOptions.length < 2"
            aria-label="分层展开程度"
          />
        </label>
        <small>
          展开只改变显示间距，不修改后端坐标。{{
            usesInferredLayers ? '旧方案缺少层号时，页面仅按相同 Z 坐标分组推断。' : ''
          }}
        </small>
      </section>

      <section v-if="selectedPlacement" class="placement-inspector" aria-live="polite">
        <div class="inspector-title">
          <div>
            <span>已选箱体</span>
            <strong>{{ selectedPlacement.itemCode }}</strong>
          </div>
          <button type="button" aria-label="关闭箱体详情" @click="selectMesh(null)">×</button>
        </div>
        <span class="box-identity">
          #{{ selectedPlacement.boxIndex }} · 第
          {{ selectedPlacement.layer ?? resolvedLayerIndex(selectedPlacement) }} 层{{
            selectedPlacement.layer == null ? '（按 Z 推断）' : ''
          }}
          ·
          {{ selectedPlacement.orientation }}
        </span>
        <dl>
          <div>
            <dt>权威坐标（左下后角）</dt>
            <dd>
              X {{ selectedPlacement.xMm }} · Y {{ selectedPlacement.yMm }} ·
              Z {{ selectedPlacement.zMm }} mm
            </dd>
          </div>
          <div>
            <dt>箱体尺寸</dt>
            <dd>
              {{ selectedPlacement.lengthMm }} × {{ selectedPlacement.widthMm }} ×
              {{ selectedPlacement.heightMm }} mm
            </dd>
          </div>
          <div>
            <dt>方向 / 毛重</dt>
            <dd>
              {{ selectedPlacement.rotationDeg == null ? '旧数据未记录' : `${selectedPlacement.rotationDeg}°` }}
              ·
              {{
                selectedPlacement.grossWeightKg == null
                  ? '旧数据未记录'
                  : `${selectedPlacement.grossWeightKg.toFixed(2)} kg`
              }}
            </dd>
          </div>
          <div v-if="selectedPlacement.placementId">
            <dt>摆放标识</dt>
            <dd class="placement-id">{{ selectedPlacement.placementId }}</dd>
          </div>
        </dl>
      </section>

      <div class="interaction-hint" aria-hidden="true">
        <span>拖动旋转</span>
        <span>滚轮缩放</span>
        <span>点击选箱</span>
        <span>双击复位</span>
      </div>
      <p class="sr-only" aria-live="polite">
        当前显示 {{ visibleBoxCount }} 箱，投影方式为
        {{ cameraMode === 'perspective' ? '透视' : '正交' }}。
      </p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.pallet-3d-panel {
  --panel-green: #245d49;
  --panel-green-strong: #174838;
  --panel-green-soft: #e9f3ee;
  --panel-border: #d6e1db;
  overflow: hidden;
  border: 1px solid var(--panel-border);
  border-radius: 16px;
  background: #f3f7f5;
  box-shadow: 0 18px 45px rgba(24, 65, 50, 0.08);
}

.pallet-3d-header {
  display: flex;
  min-height: 76px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--panel-border);
  background:
    linear-gradient(105deg, rgba(235, 246, 240, 0.9), rgba(255, 255, 255, 0) 50%),
    #fff;
}

.pallet-3d-title {
  display: grid;
  gap: 3px;
}

.pallet-3d-title strong {
  color: #183d31;
  font-size: 16px;
  font-weight: 760;
  letter-spacing: -0.01em;
}

.pallet-3d-title > span:last-child {
  color: #688077;
  font-size: 12px;
}

.pallet-3d-kicker {
  color: #4e856e;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.15em;
}

.pallet-3d-header-badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}

.pallet-3d-header-badges span {
  padding: 5px 9px;
  border: 1px solid #dce8e2;
  border-radius: 999px;
  color: #527064;
  background: #f7faf8;
  font-size: 11px;
  font-weight: 650;
}

.pallet-3d-header-badges .unit-badge {
  color: #fff;
  border-color: var(--panel-green);
  background: var(--panel-green);
}

.pallet-3d-toolbar {
  display: flex;
  min-height: 48px;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--panel-border);
  background: rgba(250, 252, 251, 0.96);
}

.toolbar-group {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px;
  border: 1px solid #d9e4de;
  border-radius: 9px;
  background: #fff;
}

.toolbar-label {
  padding: 0 6px;
  color: #81938c;
  font-size: 10px;
  font-weight: 700;
}

.toolbar-button,
.fullscreen-button {
  min-height: 29px;
  border: 0;
  border-radius: 6px;
  color: #536c62;
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  font-weight: 700;
  transition:
    color 150ms ease,
    background 150ms ease,
    box-shadow 150ms ease;
}

.toolbar-button {
  padding: 5px 9px;
}

.toolbar-button:hover,
.toolbar-button:focus-visible,
.fullscreen-button:hover,
.fullscreen-button:focus-visible {
  color: var(--panel-green-strong);
  background: #eef6f2;
  outline: none;
}

.toolbar-button.active {
  color: #fff;
  background: var(--panel-green);
  box-shadow: 0 3px 8px rgba(36, 93, 73, 0.2);
}

.toolbar-button.reset {
  color: #3e6857;
  border-left: 1px solid #e1e9e5;
  border-radius: 0 6px 6px 0;
}

.toolbar-spacer {
  flex: 1;
}

.fullscreen-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid #d5e2dc;
  background: #fff;
}

.fullscreen-button span {
  font-size: 14px;
  line-height: 1;
}

.pallet-3d-stage {
  position: relative;
  min-height: 570px;
}

.pallet-3d-stage {
  overflow: hidden;
  background:
    radial-gradient(circle at 50% 28%, rgba(255, 255, 255, 0.88), transparent 48%),
    #eaf0ed;
}

.pallet-3d-viewport {
  position: absolute;
  inset: 0;
  outline: none;
}

.pallet-3d-viewport:focus-visible {
  box-shadow: inset 0 0 0 3px rgba(43, 116, 87, 0.5);
}

.pallet-3d-viewport :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
}

.pallet-3d-viewport :deep(canvas:active) {
  cursor: grabbing;
}

.pallet-3d-empty {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  padding: 40px;
  color: #6f827a;
  background:
    radial-gradient(circle at 50% 28%, rgba(255, 255, 255, 0.92), transparent 48%),
    #eaf0ed;
  text-align: center;
}

.pallet-3d-empty > span {
  display: grid;
  width: 64px;
  height: 64px;
  place-items: center;
  border: 1px solid #d0e3d9;
  border-radius: 18px;
  color: var(--panel-green);
  background: var(--panel-green-soft);
  font-size: 14px;
  font-weight: 800;
}

.pallet-3d-empty strong {
  color: #26483b;
  font-size: 14px;
}

.pallet-3d-empty p {
  max-width: 440px;
  margin: 0;
  font-size: 12px;
  line-height: 1.65;
}

.pallet-3d-error {
  position: absolute;
  top: 14px;
  left: 50%;
  z-index: 5;
  max-width: calc(100% - 28px);
  padding: 9px 12px;
  transform: translateX(-50%);
  border: 1px solid #f1c7c3;
  border-radius: 9px;
  color: #9b433c;
  background: rgba(255, 241, 239, 0.96);
  box-shadow: 0 8px 24px rgba(122, 49, 42, 0.12);
  font-size: 11px;
}

.dimension-card,
.pallet-3d-legend,
.layer-controller,
.placement-inspector {
  position: absolute;
  z-index: 2;
  border: 1px solid rgba(255, 255, 255, 0.92);
  border-radius: 12px;
  background: rgba(250, 253, 251, 0.9);
  box-shadow: 0 10px 28px rgba(22, 58, 45, 0.11);
  backdrop-filter: blur(12px);
}

.dimension-card {
  top: 14px;
  left: 14px;
  width: 216px;
  padding: 12px;
}

.overlay-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 9px;
  color: #385c4d;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.overlay-heading > i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #4c9a77;
  box-shadow: 0 0 0 4px rgba(76, 154, 119, 0.12);
}

.overlay-heading > i.warning {
  background: #d86258;
  box-shadow: 0 0 0 4px rgba(216, 98, 88, 0.13);
}

.dimension-card dl,
.placement-inspector dl {
  display: grid;
  grid-template-columns: 1fr;
  gap: 7px;
  margin: 0;
}

.dimension-card dl > div,
.placement-inspector dl > div {
  display: grid;
  gap: 2px;
}

.dimension-card dt,
.placement-inspector dt {
  color: #81928b;
  font-size: 9px;
}

.dimension-card dd,
.placement-inspector dd {
  margin: 0;
  color: #284b3e;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.4;
}

.dimension-card dd.danger {
  color: #bd4b43;
}

.plan-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 4px;
  margin-top: 10px;
}

.plan-facts > div {
  display: grid;
  min-width: 0;
  gap: 3px;
  padding: 6px;
  border: 1px solid #e1e9e5;
  border-radius: 7px;
  background: rgba(241, 247, 244, 0.84);
}

.plan-facts span {
  color: #83958d;
  font-size: 7px;
  line-height: 1.3;
}

.plan-facts strong {
  overflow: hidden;
  color: #2f624e;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.coordinate-note {
  display: block;
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px solid #e0e8e4;
  color: #799087;
  font-size: 9px;
}

.pallet-3d-legend {
  display: block;
  top: 14px;
  right: 14px;
  width: min(238px, calc(100% - 28px));
  max-width: none;
  padding: 12px;
}

.pallet-3d-legend .overlay-heading button {
  padding: 0;
  border: 0;
  color: var(--panel-green);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 9px;
  text-transform: none;
}

.pallet-3d-legend .overlay-heading button:disabled {
  color: #a4b1ac;
  cursor: default;
}

.legend-items {
  display: grid;
  max-height: 150px;
  gap: 5px;
  overflow: auto;
}

.legend-items button {
  display: grid;
  grid-template-columns: 9px minmax(0, 1fr) auto;
  align-items: center;
  gap: 7px;
  width: 100%;
  min-height: 29px;
  padding: 5px 7px;
  border: 1px solid #dfe8e3;
  border-radius: 7px;
  color: #3d5d50;
  background: rgba(255, 255, 255, 0.78);
  cursor: pointer;
  font: inherit;
  font-size: 10px;
  text-align: left;
  transition: opacity 150ms ease;
}

.legend-items button:hover,
.legend-items button:focus-visible {
  border-color: #83aa98;
  outline: none;
}

.legend-items button.muted {
  opacity: 0.43;
}

.legend-items i {
  width: 9px;
  height: 9px;
  border-radius: 3px;
}

.legend-items span {
  display: block;
  overflow: hidden;
  padding: 0;
  border: 0;
  border-radius: 0;
  color: inherit;
  background: transparent;
  box-shadow: none;
  backdrop-filter: none;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.legend-items em {
  min-width: 21px;
  padding: 2px 5px;
  border-radius: 999px;
  color: #688178;
  background: #edf3f0;
  font-size: 9px;
  font-style: normal;
  font-weight: 750;
  text-align: center;
}

.layer-controller {
  bottom: 40px;
  left: 14px;
  width: 252px;
  padding: 12px;
}

.layer-controller .overlay-heading strong {
  color: var(--panel-green);
  font-size: 10px;
}

.layer-controller label {
  display: grid;
  gap: 5px;
  margin-top: 8px;
  color: #61786e;
  font-size: 9px;
  font-weight: 700;
}

.layer-controller select {
  width: 100%;
  height: 30px;
  padding: 0 8px;
  border: 1px solid #d7e3dd;
  border-radius: 7px;
  color: #345648;
  background: #fff;
  font: inherit;
  font-size: 10px;
}

.explode-control > span {
  display: flex;
  justify-content: space-between;
}

.explode-control output {
  color: var(--panel-green);
  font-weight: 800;
}

.explode-control input {
  width: 100%;
  accent-color: var(--panel-green);
}

.explode-control input:disabled {
  opacity: 0.45;
}

.layer-controller small {
  display: block;
  margin-top: 7px;
  color: #899991;
  font-size: 8px;
  line-height: 1.45;
}

.placement-inspector {
  right: 14px;
  bottom: 40px;
  width: min(292px, calc(100% - 28px));
  padding: 13px;
}

.inspector-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.inspector-title > div {
  display: grid;
  gap: 2px;
}

.inspector-title span {
  color: #80928a;
  font-size: 9px;
}

.inspector-title strong {
  color: #20483a;
  font-size: 14px;
}

.inspector-title button {
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  padding: 0;
  border: 1px solid #dce6e1;
  border-radius: 7px;
  color: #61786e;
  background: #fff;
  cursor: pointer;
  font-size: 16px;
}

.inspector-title button:hover,
.inspector-title button:focus-visible {
  color: #a23f38;
  border-color: #e7b9b5;
  outline: none;
}

.box-identity {
  display: block;
  margin: 6px 0 10px;
  color: #648075;
  font-size: 9px;
}

.placement-id {
  overflow-wrap: anywhere;
}

.interaction-hint {
  position: absolute;
  bottom: 10px;
  left: 50%;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 5px;
  transform: translateX(-50%);
  color: #658076;
  font-size: 8px;
  white-space: nowrap;
}

.interaction-hint span {
  padding: 3px 6px;
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.62);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.pallet-3d-panel:fullscreen,
.pallet-3d-panel.is-fullscreen {
  display: flex;
  width: 100vw;
  height: 100vh;
  flex-direction: column;
  border: 0;
  border-radius: 0;
  background: #eef3f0;
}

.pallet-3d-panel:fullscreen .pallet-3d-stage,
.pallet-3d-panel.is-fullscreen .pallet-3d-stage {
  min-height: 0;
  flex: 1;
}

@media (max-width: 760px) {
  .pallet-3d-header {
    align-items: flex-start;
  }

  .pallet-3d-toolbar {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .toolbar-spacer {
    display: none;
  }

  .fullscreen-button {
    margin-left: auto;
  }

  .pallet-3d-stage {
    min-height: 720px;
  }

  .dimension-card,
  .pallet-3d-legend {
    width: calc(50% - 21px);
  }

  .layer-controller {
    bottom: 38px;
    width: calc(50% - 21px);
  }

  .placement-inspector {
    right: 14px;
    bottom: 38px;
    width: calc(50% - 21px);
  }
}

@media (max-width: 540px) {
  .pallet-3d-header {
    display: grid;
  }

  .pallet-3d-header-badges {
    justify-content: flex-start;
  }

  .toolbar-group {
    width: 100%;
    overflow-x: auto;
  }

  .toolbar-spacer {
    display: none;
  }

  .fullscreen-button {
    width: 100%;
    justify-content: center;
  }

  .pallet-3d-stage {
    min-height: 860px;
  }

  .dimension-card,
  .pallet-3d-legend,
  .layer-controller,
  .placement-inspector {
    right: 12px;
    left: 12px;
    width: auto;
  }

  .dimension-card {
    top: 12px;
  }

  .pallet-3d-legend {
    top: 154px;
  }

  .layer-controller {
    bottom: 40px;
  }

  .placement-inspector {
    bottom: 210px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .toolbar-button,
  .fullscreen-button,
  .legend-items button {
    transition: none;
  }
}
</style>
