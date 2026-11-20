import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'

// Vite 不会自动为 Monaco 的语言服务配置 worker，需要在应用启动时显式提供。
globalThis.MonacoEnvironment = {
  getWorker(_moduleId, label) {
    if (label === 'json') {
      return new JsonWorker()
    }

    return new EditorWorker()
  },
}
