<script>
  import { onMount } from 'svelte';
  import { Editor } from '@tiptap/core';
  import StarterKit from '@tiptap/starter-kit';
  import { Markdown } from '@tiptap/markdown';
  import { Table } from '@tiptap/extension-table';
  import { TableRow } from '@tiptap/extension-table-row';
  import { TableCell } from '@tiptap/extension-table-cell';
  import { TableHeader } from '@tiptap/extension-table-header';
  import {
    Bold,
    Code2,
    Heading1,
    Heading2,
    Italic,
    Link,
    List,
    ListOrdered,
    Pilcrow,
    Quote,
    Redo2,
    Table2,
    Undo2
  } from '@lucide/svelte';

  export let content = '';
  export let onChange = () => {};

  let element;
  let editorState = { editor: null };

  onMount(() => {
    const editor = new Editor({
      element,
      extensions: [
        StarterKit.configure({ link: { openOnClick: false } }),
        Table.configure({ resizable: true }),
        TableRow,
        TableHeader,
        TableCell,
        Markdown.configure({ markedOptions: { gfm: true } })
      ],
      content,
      contentType: 'markdown',
      editorProps: { attributes: { class: 'wysiwyg-content' } },
      onUpdate: ({ editor }) => onChange(editor.getMarkdown()),
      onTransaction: ({ editor }) => { editorState = { editor }; }
    });
    editorState = { editor };
    return () => editor.destroy();
  });

  function setLink() {
    const editor = editorState.editor;
    if (!editor) return;
    const current = editor.getAttributes('link').href || '';
    const href = window.prompt('Link URL', current);
    if (href === null) return;
    if (!href.trim()) editor.chain().focus().extendMarkRange('link').unsetLink().run();
    else editor.chain().focus().extendMarkRange('link').setLink({ href: href.trim() }).run();
  }
</script>

<div class="wysiwyg-editor">
  {#if editorState.editor}
    <div class="wysiwyg-toolbar" aria-label="Markdown formatting">
      <button type="button" class:active={editorState.editor.isActive('paragraph')} onclick={() => editorState.editor.chain().focus().setParagraph().run()} title="Paragraph"><Pilcrow size={14} /></button>
      <button type="button" class:active={editorState.editor.isActive('heading', { level: 1 })} onclick={() => editorState.editor.chain().focus().toggleHeading({ level: 1 }).run()} title="Heading 1"><Heading1 size={14} /></button>
      <button type="button" class:active={editorState.editor.isActive('heading', { level: 2 })} onclick={() => editorState.editor.chain().focus().toggleHeading({ level: 2 }).run()} title="Heading 2"><Heading2 size={14} /></button>
      <span></span>
      <button type="button" class:active={editorState.editor.isActive('bold')} onclick={() => editorState.editor.chain().focus().toggleBold().run()} title="Bold"><Bold size={14} /></button>
      <button type="button" class:active={editorState.editor.isActive('italic')} onclick={() => editorState.editor.chain().focus().toggleItalic().run()} title="Italic"><Italic size={14} /></button>
      <button type="button" class:active={editorState.editor.isActive('link')} onclick={setLink} title="Link"><Link size={14} /></button>
      <button type="button" class:active={editorState.editor.isActive('code')} onclick={() => editorState.editor.chain().focus().toggleCode().run()} title="Inline code"><Code2 size={14} /></button>
      <span></span>
      <button type="button" class:active={editorState.editor.isActive('bulletList')} onclick={() => editorState.editor.chain().focus().toggleBulletList().run()} title="Bullet list"><List size={14} /></button>
      <button type="button" class:active={editorState.editor.isActive('orderedList')} onclick={() => editorState.editor.chain().focus().toggleOrderedList().run()} title="Numbered list"><ListOrdered size={14} /></button>
      <button type="button" class:active={editorState.editor.isActive('blockquote')} onclick={() => editorState.editor.chain().focus().toggleBlockquote().run()} title="Quote"><Quote size={14} /></button>
      <button type="button" onclick={() => editorState.editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()} title="Insert table"><Table2 size={14} /></button>
      <span class="toolbar-spacer"></span>
      <button type="button" disabled={!editorState.editor.can().undo()} onclick={() => editorState.editor.chain().focus().undo().run()} title="Undo"><Undo2 size={14} /></button>
      <button type="button" disabled={!editorState.editor.can().redo()} onclick={() => editorState.editor.chain().focus().redo().run()} title="Redo"><Redo2 size={14} /></button>
    </div>
  {/if}
  <div class="wysiwyg-surface" bind:this={element}></div>
</div>
