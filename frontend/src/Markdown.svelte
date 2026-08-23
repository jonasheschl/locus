<script>
  import DOMPurify from 'dompurify';
  import { marked } from 'marked';

  export let content = '';
  export let compact = false;

  marked.setOptions({ gfm: true, breaks: false });

  function renderMarkdown(value) {
    const withWikiLinks = (value || '').replace(
      /\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]/g,
      (_, path, alias) => `[${alias || path}](#/note/${encodeURIComponent(path.trim())})`
    );
    return DOMPurify.sanitize(marked.parse(withWikiLinks), {
      USE_PROFILES: { html: true }
    });
  }

  $: html = renderMarkdown(content);
</script>

<div class:compact class="markdown-body">{@html html}</div>
