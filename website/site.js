// The website's few behaviors: a line under the top bar once scrolled, sections that fade in,
// and copy buttons on code blocks. Everything works without it.
document.documentElement.classList.add('js');

const topbar = document.querySelector('.topbar');
const onScroll = () => topbar.classList.toggle('scrolled', scrollY > 8);
addEventListener('scroll', onScroll, { passive: true });
onScroll();

const reveal = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add('in');
        reveal.unobserve(e.target);
      }
    }
  },
  { rootMargin: '0px 0px -8% 0px' }
);
document.querySelectorAll('.reveal').forEach((el) => reveal.observe(el));

document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    const text = document.getElementById(button.dataset.copy).innerText;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = 'Copied';
    } catch {
      button.textContent = 'Select and copy';
    }
    setTimeout(() => (button.textContent = 'Copy'), 1600);
  });
});
