document.querySelectorAll('form').forEach(form => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"],button:not([type])');
    if (button && !button.dataset.noLoading) {
      button.dataset.originalText = button.textContent;
      button.textContent = 'Please wait…';
      button.disabled = true;
    }
  });
});
document.querySelectorAll('input[name="code"],input[name="class_code"]').forEach(el => {
  el.addEventListener('input', () => { el.value = el.value.toUpperCase(); });
});
