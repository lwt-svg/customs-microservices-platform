<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

import type {
  LoadPlanningPalletSpec,
  LoadPlanningPlacement,
  LoadPlanningPlanPallet,
} from '@/api/platform'

const props = defineProps<{
  pallet: LoadPlanningPlanPallet | null
  palletSpec: LoadPlanningPalletSpec | null
}>()

const viewport = ref<HTMLDivElement | null>(null)
const renderError = ref('')
const selectedPlacement = ref<LoadPlanningPlacement | null>(null)

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let contentGroup: THREE.Group | null = null
let resizeObserver: ResizeObserver | null = null
let animationFrame = 0
let selectedMesh: THREE.Mesh | null = null

/**
 * 后端坐标使用 mm；Three.js 场景统一缩放成“米级”世界坐标，避免相机裁剪面跨度过大。
 *
 * 坐标映射：
 * - 后端 x → Three.js X；
 * - 后端 y → Three.js Z（托盘纵深）；
 * - 后端 z → Three.js Y（竖直方向）。
 *
 * 页面绝不重新计算装载坐标，只做可视化映射。
 */
const MM_TO_WORLD = 0.001

const legend = computed(() => {
  const codes = new Set(props.pallet?.placements.map((placement) => placement.itemCode) ?? [])
  return [...codes].map((itemCode) => ({
    itemCode,
    color: colorForItem(itemCode),
  }))
})

function colorForItem(itemCode: string): string {
  let hash = 0
  for (const char of itemCode) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  const hue = 145 + (hash % 185)
  return `hsl(${hue} 48% 52%)`
}

function disposeObject(object: THREE.Object3D): void {
  object.traverse((child) => {
    if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
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

function clearContent(): void {
  if (!contentGroup) return
  for (const child of [...contentGroup.children]) {
    contentGroup.remove(child)
    disposeObject(child)
  }
  selectedMesh = null
  selectedPlacement.value = null
}

function addBox(
  placement: LoadPlanningPlacement,
  palletLengthMm: number,
  palletWidthMm: number,
): void {
  if (!contentGroup) return

  const length = placement.lengthMm * MM_TO_WORLD
  const width = placement.widthMm * MM_TO_WORLD
  const height = placement.heightMm * MM_TO_WORLD
  const geometry = new THREE.BoxGeometry(length, height, width)
  const material = new THREE.MeshStandardMaterial({
    color: colorForItem(placement.itemCode),
    roughness: 0.72,
    metalness: 0.02,
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
  mesh.castShadow = true
  mesh.receiveShadow = true

  const edgeGeometry = new THREE.EdgesGeometry(geometry)
  const edges = new THREE.LineSegments(
    edgeGeometry,
    new THREE.LineBasicMaterial({ color: 0x173d34, transparent: true, opacity: 0.48 }),
  )
  mesh.add(edges)
  contentGroup.add(mesh)
}

function rebuildScene(): void {
  if (!scene || !camera || !controls || !props.pallet || !props.palletSpec) {
    clearContent()
    return
  }

  clearContent()
  const spec = props.palletSpec
  const palletLength = spec.lengthMm * MM_TO_WORLD
  const palletWidth = spec.widthMm * MM_TO_WORLD
  const palletHeight = spec.heightMm * MM_TO_WORLD

  const palletGeometry = new THREE.BoxGeometry(palletLength, palletHeight, palletWidth)
  const palletMaterial = new THREE.MeshStandardMaterial({
    color: 0xb5895a,
    roughness: 0.88,
  })
  const palletMesh = new THREE.Mesh(palletGeometry, palletMaterial)
  palletMesh.position.y = palletHeight / 2
  palletMesh.receiveShadow = true
  palletMesh.castShadow = true
  contentGroup?.add(palletMesh)

  const palletEdges = new THREE.LineSegments(
    new THREE.EdgesGeometry(palletGeometry),
    new THREE.LineBasicMaterial({ color: 0x654321 }),
  )
  palletEdges.position.copy(palletMesh.position)
  contentGroup?.add(palletEdges)

  for (const placement of props.pallet.placements) {
    addBox(placement, spec.lengthMm, spec.widthMm)
  }

  const loadedHeight = Math.max(props.pallet.loadedHeightMm, spec.heightMm) * MM_TO_WORLD
  const span = Math.max(palletLength, palletWidth, loadedHeight, 0.8)
  camera.near = Math.max(span / 100, 0.001)
  camera.far = span * 30
  camera.position.set(span * 1.35, span * 1.1, span * 1.45)
  camera.updateProjectionMatrix()
  controls.target.set(0, loadedHeight * 0.42, 0)
  controls.minDistance = span * 0.55
  controls.maxDistance = span * 5
  controls.update()
}

function resizeRenderer(): void {
  if (!viewport.value || !renderer || !camera) return
  const width = Math.max(viewport.value.clientWidth, 1)
  const height = Math.max(viewport.value.clientHeight, 1)
  renderer.setSize(width, height, false)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  camera.aspect = width / height
  camera.updateProjectionMatrix()
}

function selectMesh(mesh: THREE.Mesh | null): void {
  if (selectedMesh) {
    const previous = selectedMesh.material
    if (previous instanceof THREE.MeshStandardMaterial) previous.emissive.setHex(0x000000)
  }
  selectedMesh = mesh
  selectedPlacement.value = (mesh?.userData.placement as LoadPlanningPlacement | undefined) ?? null
  if (mesh) {
    const material = mesh.material
    if (material instanceof THREE.MeshStandardMaterial) material.emissive.setHex(0x2f5f50)
  }
}

function handlePointer(event: PointerEvent): void {
  if (!renderer || !camera || !contentGroup) return
  const bounds = renderer.domElement.getBoundingClientRect()
  const pointer = new THREE.Vector2(
    ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
  )
  const raycaster = new THREE.Raycaster()
  raycaster.setFromCamera(pointer, camera)
  const meshes = contentGroup.children.filter(
    (child): child is THREE.Mesh =>
      child instanceof THREE.Mesh && child.userData.placement !== undefined,
  )
  const hit = raycaster.intersectObjects(meshes, false)[0]
  selectMesh(hit?.object instanceof THREE.Mesh ? hit.object : null)
}

function animate(): void {
  animationFrame = window.requestAnimationFrame(animate)
  controls?.update()
  if (scene && camera && renderer) renderer.render(scene, camera)
}

onMounted(async () => {
  await nextTick()
  if (!viewport.value) return

  try {
    scene = new THREE.Scene()
    scene.background = new THREE.Color(0xf6f8f7)
    scene.fog = new THREE.Fog(0xf6f8f7, 3, 12)
    camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100)
    renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.shadowMap.enabled = true
    // Three.js r186 已移除 PCFSoftShadowMap，并会在控制台自动回退。直接使用仍受支持的
    // PCFShadowMap，既保留装托复核需要的阴影层次，也避免每次打开工作台产生兼容警告。
    renderer.shadowMap.type = THREE.PCFShadowMap
    renderer.domElement.addEventListener('pointerdown', handlePointer)
    viewport.value.appendChild(renderer.domElement)

    controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08

    contentGroup = new THREE.Group()
    scene.add(contentGroup)

    scene.add(new THREE.HemisphereLight(0xffffff, 0x54685e, 1.55))
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.1)
    keyLight.position.set(4, 7, 5)
    keyLight.castShadow = true
    scene.add(keyLight)

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(12, 12),
      new THREE.MeshStandardMaterial({ color: 0xe9eeeb, roughness: 1 }),
    )
    floor.rotation.x = -Math.PI / 2
    floor.receiveShadow = true
    scene.add(floor)

    const grid = new THREE.GridHelper(12, 24, 0xb6c4bd, 0xdde5e1)
    grid.position.y = 0.001
    scene.add(grid)

    resizeObserver = new ResizeObserver(resizeRenderer)
    resizeObserver.observe(viewport.value)
    resizeRenderer()
    rebuildScene()
    animate()
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

onBeforeUnmount(() => {
  window.cancelAnimationFrame(animationFrame)
  resizeObserver?.disconnect()
  if (renderer) {
    renderer.domElement.removeEventListener('pointerdown', handlePointer)
    renderer.dispose()
    renderer.forceContextLoss()
    renderer.domElement.remove()
  }
  controls?.dispose()
  if (scene) disposeObject(scene)
  scene = null
  camera = null
  renderer = null
  controls = null
  contentGroup = null
})
</script>

<template>
  <section class="pallet-3d-panel">
    <div class="pallet-3d-header">
      <div>
        <strong>交互式三维复核</strong>
        <span>拖动旋转 · 滚轮缩放 · 点击箱子查看坐标</span>
      </div>
      <span class="unit-badge">后端坐标 · mm</span>
    </div>

    <div v-if="!pallet || !palletSpec" class="pallet-3d-empty">
      <span>3D</span>
      <strong>请选择包含已放置箱子的托盘</strong>
      <p>三维视图只展示后端算法返回的坐标，不会在浏览器中重新排箱。</p>
    </div>
    <div v-else class="pallet-3d-stage">
      <div ref="viewport" class="pallet-3d-viewport"></div>
      <div v-if="renderError" class="pallet-3d-error">{{ renderError }}</div>

      <div class="pallet-3d-legend">
        <span v-for="entry in legend" :key="entry.itemCode">
          <i :style="{ background: entry.color }"></i>
          {{ entry.itemCode }}
        </span>
      </div>

      <div v-if="selectedPlacement" class="placement-inspector">
        <div>
          <strong>{{ selectedPlacement.itemCode }}</strong>
          <span>#{{ selectedPlacement.boxIndex }} · {{ selectedPlacement.orientation }}</span>
        </div>
        <dl>
          <div>
            <dt>坐标（左下后角）</dt>
            <dd>
              {{ selectedPlacement.xMm }}, {{ selectedPlacement.yMm }},
              {{ selectedPlacement.zMm }} mm
            </dd>
          </div>
          <div>
            <dt>尺寸</dt>
            <dd>
              {{ selectedPlacement.lengthMm }} × {{ selectedPlacement.widthMm }} ×
              {{ selectedPlacement.heightMm }} mm
            </dd>
          </div>
        </dl>
      </div>
    </div>
  </section>
</template>
