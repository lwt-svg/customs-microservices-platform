/**
 * 告诉 VS Code 的 TypeScript 服务：所有 .vue 文件都是 Vue 组件模块。
 *
 * vue-tsc 本身能够识别 .vue 文件，但编辑器内置的 TypeScript 服务在没有
 * Vue 官方扩展参与时可能仍然报“找不到模块”。这个声明用于消除该误报。
 */
declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}
