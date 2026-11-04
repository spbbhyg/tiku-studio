<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { Editor, EditorContent } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Image from '@tiptap/extension-image'
import { MathInline, emptyDoc, MATH_SYMBOLS } from './rich.js'
import { ChemInline } from './chem.js'
import ChemPanel from './ChemPanel.vue'
import { api } from '../../api.js'

const props = defineProps({
  modelValue: { type: Object, default: () => emptyDoc() },
  placeholder: { type: String, default: '' },
  minHeight: { type: Number, default: 90 },
  toolbar: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue'])

const showMath = ref(false)
const showChem = ref(false)
const inserting = ref(false)
const latex = ref('')
const mathInput = ref(null)

const editor = new Editor({
  content: props.modelValue || emptyDoc(),
  extensions: [
      StarterKit,
      MathInline,
      ChemInline,
      Image.configure({ inline: false, allowBase64: true, HTMLAttributes: { class: 'rich-img' } }),
    ],
  editorProps: {
    attributes: {
      class: 'rich-body',
      style: `min-height:${props.minHeight}px`,
      'data-placeholder': props.placeholder,
    },
  },
  onUpdate: ({ editor: ed }) => emit('update:modelValue', ed.getJSON()),
})

let syncing = false
watch(
  () => props.modelValue,
  (val) => {
    if (!val || syncing) return
    const cur = JSON.stringify(editor.getJSON())
    if (cur === JSON.stringify(val)) return
    syncing = true
    editor.commands.setContent(val, false)
    syncing = false
  }
)

function insertMath(text) {
  const value = (text ?? latex.value).trim()
  if (!value) return
  editor.chain().focus().insertContent({ type: 'mathInline', attrs: { latex: value } }).run()
  if (text == null) latex.value = ''
}
function pushSymbol(sym) {
  // 有选区就套在公式里；否则直接插入空壳让用户填
  if (sym === '^' || sym === '_') {
    latex.value += sym + '{}'
  } else {
    latex.value += sym
  }
  if (mathInput.value) mathInput.value.focus()
}
function toggleMath() {
  showMath.value = !showMath.value
}

async function onChemInsert(data) {
  inserting.value = true
  try {
    // 结构式面板已经存过库，这里只负责插入节点
    editor.chain().focus().insertContent({
      type: 'chemInline',
      attrs: { chemId: data.chemId || 0, svg: data.svg || '', molfile: data.molfile || '' },
    }).run()
    showChem.value = false
  } catch (e) {
    alert('保存结构式失败：' + e.message)
  } finally {
    inserting.value = false
  }
}

onBeforeUnmount(() => editor.destroy())
function insertImage(src, materialId) {
  if (!src) return
  const attrs = { src }
  if (materialId) attrs.alt = 'material:' + materialId
  editor.chain().focus().setImage(attrs).run()
}

defineExpose({ insertMath, insertImage, focus: () => editor.commands.focus() })
</script>

<template>
  <div class="rich">
    <div v-if="toolbar" class="rtool">
      <button type="button" class="tb" :class="{ on: editor.isActive('bold') }"
              @click="editor.chain().focus().toggleBold().run()" title="加粗 Ctrl+B"><b>B</b></button>
      <button type="button" class="tb" :class="{ on: editor.isActive('italic') }"
              @click="editor.chain().focus().toggleItalic().run()" title="斜体 Ctrl+I"><i>I</i></button>
      <button type="button" class="tb" :class="{ on: editor.isActive('bulletList') }"
              @click="editor.chain().focus().toggleBulletList().run()" title="无序列表">•</button>
      <button type="button" class="tb" :class="{ on: editor.isActive('orderedList') }"
              @click="editor.chain().focus().toggleOrderedList().run()" title="有序列表">1.</button>
      <button type="button" class="tb" @click="editor.chain().focus().undo().run()" title="撤销">↶</button>
      <button type="button" class="tb" @click="editor.chain().focus().redo().run()" title="重做">↷</button>
      <span class="sep"></span>
      <button type="button" class="tb math" :class="{ on: showMath }" @click="toggleMath"
              title="插入公式（行内，KaTeX）">∑ 公式</button>
      <button type="button" class="tb" :class="{ on: showChem }" @click="showChem = !showChem"
              title="插入结构式（Ketcher 侧边面板）">⬡ 结构式</button>
    </div>

    <div v-if="showMath" class="mpop">
      <div class="mrow">
        <input ref="mathInput" v-model="latex" class="minput" placeholder="如 \frac{ΔH}{T} 或 \xrightarrow{FeBr_3}"
               @keyup.enter="insertMath()" />
        <button type="button" class="mini primary" @click="insertMath()">插入</button>
        <button type="button" class="mini" @click="showMath = false">关闭</button>
      </div>
      <div class="msyms">
        <button v-for="s in MATH_SYMBOLS" :key="s[0]" type="button" class="sym" :title="s[0]"
                @click="pushSymbol(s[0])">{{ s[1] }}</button>
      </div>
      <div class="mhint">回车插入；点符号追加到输入框；`\xrightarrow{条件}` 用于反应箭头。</div>
    </div>

    <EditorContent :editor="editor" class="rbody" />
    <ChemPanel v-if="showChem" @insert="onChemInsert" @close="showChem = false" />
  </div>
</template>

<style scoped>
.rich { border: 1px solid var(--line); border-radius: 10px; background: #fff; overflow: hidden; }
.rtool { display: flex; align-items: center; gap: 4px; padding: 6px 8px; border-bottom: 1px solid var(--line); background: #fafbfc; }
.tb { min-width: 28px; height: 26px; border: 1px solid transparent; background: transparent; border-radius: 6px; cursor: pointer; color: #344054; font-size: 13px; padding: 0 6px; }
.tb:hover { background: #eef2f7; }
.tb.on { background: var(--brand-soft); color: var(--brand); border-color: #cfe0ff; }
.tb.math { margin-left: 2px; }
.sep { width: 1px; height: 16px; background: var(--line); margin: 0 4px; }
.mpop { padding: 8px; border-bottom: 1px solid var(--line); background: #f7f9fc; display: flex; flex-direction: column; gap: 6px; }
.mrow { display: flex; gap: 6px; }
.minput { flex: 1; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; font-family: ui-monospace, Consolas, monospace; font-size: 13px; }
.mini { padding: 5px 10px; border: 1px solid var(--line); background: #fff; border-radius: 6px; cursor: pointer; }
.mini.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.msyms { display: flex; flex-wrap: wrap; gap: 4px; }
.sym { min-width: 30px; height: 26px; border: 1px solid var(--line); background: #fff; border-radius: 5px; cursor: pointer; font-size: 13px; }
.sym:hover { border-color: var(--brand); color: var(--brand); }
.mhint { font-size: 12px; color: #98a2b3; }
.rbody :deep(.rich-body) { padding: 10px 12px; font-size: 14.5px; line-height: 1.75; outline: none; }
.rbody :deep(.rich-body p) { margin: 0 0 6px; }
.rbody :deep(.rich-body[data-placeholder]:empty::before),
.rbody :deep(.rich-body p.is-editor-empty:first-child::before) {
  content: attr(data-placeholder); color: #9aa4b2; float: left; height: 0; pointer-events: none;
}
.rbody :deep(.math-inline) { display: inline-block; padding: 0 2px; }
.rbody :deep(ul), .rbody :deep(ol) { margin: 4px 0 6px 22px; }
</style>
