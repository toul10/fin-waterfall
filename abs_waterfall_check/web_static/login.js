document.querySelector('form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = form.querySelector('[role="alert"]');
  try {
    const response = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    if (response.ok) location.replace('/' + location.hash);
    else message.textContent = 'Sign-in failed. Check your username and password.';
  } catch {
    message.textContent = 'The workspace is unavailable. Try again.';
  }
});
