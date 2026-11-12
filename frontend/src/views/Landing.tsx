import { useEffect, useRef } from 'react';
import { Icon, LinkButton } from '../components/ui';

const accentShapes = {
  star: 'M48.01 7.59Q50.00 3.00 51.99 7.59L61.01 28.41Q63.00 33.00 67.98 33.45L91.02 35.55Q96.00 36.00 92.25 39.30L74.75 54.70Q71.00 58.00 72.01 62.90L76.99 87.10Q78.00 92.00 73.73 89.41L54.27 77.59Q50.00 75.00 45.73 77.59L26.27 89.41Q22.00 92.00 23.01 87.10L27.99 62.90Q29.00 58.00 25.25 54.70L7.75 39.30Q4.00 36.00 8.98 35.55L32.02 33.45Q37.00 33.00 38.99 28.41Z',
  bolt: 'M58.86 3.00Q56.00 3.00 54.31 5.31L20.36 51.77Q18.00 55.00 22.00 55.00L40.00 55.00Q44.00 55.00 43.16 58.91L35.84 93.09Q35.00 97.00 37.53 93.90L81.47 40.10Q84.00 37.00 80.00 37.00L61.00 37.00Q57.00 37.00 58.33 33.23L68.05 5.70Q69.00 3.00 66.14 3.00Z',
  sparkle: 'M50 2C56 35 65 44 98 50 65 56 56 65 50 98 44 65 35 56 2 50 35 44 44 35 50 2Z',
};
function SceneDecor({ scene }: { scene: 'hero' | 'journey' | 'inside' }) {
  const shapes: Record<typeof scene, (keyof typeof accentShapes)[]> = { hero: ['star', 'bolt'], journey: ['bolt'], inside: ['sparkle', 'star'] };
  return <div className={`scene-decor scene-decor--${scene}`} aria-hidden="true">
    {shapes[scene].map((shape, index) => <svg key={shape} className={`solid-accent solid-accent--${index + 1}`} viewBox="0 0 100 100">
      <defs><linearGradient id={`accent-${scene}-${index}`} x1="0" y1="0" x2="1" y2="1"><stop stopColor={index ? '#fff8b4' : '#a2f3ff'}/><stop offset=".48" stopColor={index ? '#ffca7b' : '#b8a8ff'}/><stop offset="1" stopColor={index ? '#ff8bc6' : '#ffa6e5'}/></linearGradient></defs>
      <path d={accentShapes[shape]} fill={`url(#accent-${scene}-${index})`}/>
    </svg>)}
  </div>;
}

function RequestIllustration() {
  return <div className="hero-workflow" aria-label="Как работать с ZeroTrace">
    <span className="landing-kicker">ОТ ЗАДЕРЖКИ К РЕШЕНИЮ</span>
    <ol className="hero-workflow-steps">
      <li><span className="hero-workflow-number">01</span><div><h3>Найдите медленный запрос</h3><p>Посмотрите, какой сервис задерживает ответ.</p></div></li>
      <li><span className="hero-workflow-number">02</span><div><h3>Проверьте настройки</h3><p>Выявите проблемы с тайм-аутами и повторными попытками.</p></div></li>
      <li><span className="hero-workflow-number">03</span><div><h3>Испытайте исправление</h3><p>Запустите тест на стенде и оцените результат.</p></div></li>
    </ol>
  </div>;
}

export function Landing() {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const compact = window.matchMedia('(max-width: 760px)');
    const scenes = Array.from(element.querySelectorAll<HTMLElement>('.scroll-scene'));
    let frame = 0;
    const clamp = (value: number) => Math.max(0, Math.min(1, value));
    const update = () => {
      frame = 0;
      const enabled = !reduced.matches && !compact.matches;
      element.dataset.scrollMotion = enabled ? 'on' : 'off';
      const height = window.innerHeight;
      const measurements = scenes.map(scene => scene.getBoundingClientRect());
      element.dataset.ending = measurements[measurements.length - 1].top < height * .5 ? 'true' : 'false';
      scenes.forEach((scene, index) => {
        const box = measurements[index];
        const visible = Math.max(0, Math.min(box.bottom, height) - Math.max(box.top, 0)) / Math.min(box.height, height);
        // Keep entrance animations mounted after their first appearance.
        // Resetting this flag removes the animation and restarts it on return.
        if (visible >= .55) scene.dataset.entered = 'true';
        const enter = enabled ? clamp((height - box.top) / (height * .65)) : 1;
        const leave = enabled && !compact.matches ? clamp(-box.bottom / height + 1) : 0;
        scene.style.setProperty('--scene-opacity', String((.12 + .88 * enter) * (1 - leave * .82)));
        scene.style.setProperty('--scene-y', `${(1 - enter) * (compact.matches ? 20 : 64) - leave * 32}px`);
        scene.style.setProperty('--scene-scale', String(1 - leave * .035));
        scene.style.setProperty('--scene-light', String(1 - leave * .5));
      });
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(update); };
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    reduced.addEventListener('change', schedule);
    compact.addEventListener('change', schedule);
    const resize = new ResizeObserver(schedule);
    scenes.forEach(scene => { resize.observe(scene); if (scene.firstElementChild) resize.observe(scene.firstElementChild); });
    update();
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      reduced.removeEventListener('change', schedule);
      compact.removeEventListener('change', schedule);
      resize.disconnect();
    };
  }, []);
  return <div ref={root} className="landing">
    <div className="landing-atmosphere" aria-hidden="true"><span/><span/><span/></div>
    <svg className="page-ribbon" viewBox="0 0 1400 2700" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="page-ribbon-color" x1="0" y1="0" x2="1" y2="1"><stop className="ornament-color ornament-color--blue" stopColor="#88baff"/><stop className="ornament-color ornament-color--mint" offset=".5" stopColor="#91e4ce"/><stop className="ornament-color ornament-color--pink" offset="1" stopColor="#eeb1da"/></linearGradient></defs><path d="M1450 80C700-80 1420 660 720 850S-180 1150 300 1550 1500 1700 1080 2120 100 2310-80 2630" fill="none" stroke="url(#page-ribbon-color)" strokeWidth="110"/></svg>
    <div className="scroll-scene"><section className="scene-content landing-hero">
      <SceneDecor scene="hero"/>
      <div className="landing-wordmark" aria-label="ZeroTrace"><span className="brand-mark" aria-hidden="true"><i/><i/><i/></span><span>Zero<span className="wordmark-trace">Trace</span><span className="wordmark-dot">.</span></span></div>
      <div className="landing-hero-grid">
        <div className="landing-intro">
          <span className="landing-kicker"><span/>Диагностика микросервисов в одном месте</span>
          <h1>Найдите место,<br/><em>где ваш сервис тормозит.</em></h1>
          <p>ZeroTrace показывает задержки между сервисами, находит проблемы в настройках и помогает проверить исправления на тестовом стенде.</p>
          <div className="landing-actions"><LinkButton href="#/traces" icon="arrow" primary>Открыть ZeroTrace</LinkButton><LinkButton href="#/sources" icon="play">Попробовать демо</LinkButton></div>
          <p className="landing-note">Начните с демоданных или подключите свои сервисы через OpenTelemetry.</p>
        </div>
        <RequestIllustration/>
      </div>
    </section></div>
    <div className="scroll-scene"><section className="scene-content landing-journey" aria-labelledby="journey-title">
      <SceneDecor scene="journey"/>
      <div className="landing-section-heading"><span className="landing-kicker">НАЙТИ. РАЗОБРАТЬСЯ. ПРОВЕРИТЬ.</span><h2 id="journey-title">От медленного запроса<br/><span>к плану исправления.</span></h2><p>Карта сервисов, аудит настроек и тестовый стенд связаны в одном приложении. Найдите проблемный участок и проверьте, что можно улучшить.</p></div>
      <div className="landing-steps">
        <article><svg className="stage-connector" viewBox="0 0 64 64" aria-hidden="true"><path d="M2 18C28 18 28 46 54 46"/><path className="stage-arrow" d="m48 40 8 6-8 6"/></svg><span className="step-number">01</span><span className="step-icon"><Icon name="graph"/></span><h3>Найдите задержку</h3><p>Откройте карту сервисов и трейс запроса. Посмотрите, какой вызов занял больше всего времени и где возникла ошибка.</p><a href="#/services">Посмотреть сервисы <Icon name="arrow"/></a></article>
        <article><svg className="stage-connector" viewBox="0 0 64 64" aria-hidden="true"><path d="M2 18C28 18 28 46 54 46"/><path className="stage-arrow" d="m48 40 8 6-8 6"/></svg><span className="step-number">02</span><span className="step-icon"><Icon name="search"/></span><h3>Проверьте настройки</h3><p>Аудит выявит проблемы с тайм-аутами и повторными попытками. Для каждого вывода — объяснение, расчёт и связанные запросы.</p><a href="#/audit">Открыть аудит <Icon name="arrow"/></a></article>
        <article><span className="step-number">03</span><span className="step-icon"><Icon name="play"/></span><h3>Испытайте изменения</h3><p>Измените настройки на тестовом стенде, запустите запросы и оцените задержки и ошибки до применения в рабочей системе.</p><a href="#/lab">Перейти к стенду <Icon name="arrow"/></a></article>
      </div>
    </section></div>
    <div className="scroll-scene"><section className="scene-content landing-under-hood" aria-labelledby="under-hood-title">
      <SceneDecor scene="inside"/>
      <div className="inside-intro"><span className="landing-kicker">ВЫВОДЫ, КОТОРЫЕ МОЖНО ПРОВЕРИТЬ</span><h2 id="under-hood-title">За каждой проблемой —<br/><em>конкретные данные.</em></h2><p>Откройте результат аудита и посмотрите, какие запросы и настройки привели к выводу. ZeroTrace показывает расчёты и сообщает, если данных для проверки не хватает.</p><div className="inside-flow"><span><Icon name="graph"/>Трейс запроса</span><i/><span><Icon name="search"/>Аудит настроек</span><i/><span><Icon name="check"/>Тестовый запуск</span></div></div>
      <div className="landing-questions">
        <details open><summary>Как подключить свои сервисы?<Icon name="chevron"/></summary><p>Подключите отправку трейсов через OpenTelemetry. Трейс — это запись пути запроса: какие сервисы он вызвал, сколько длился каждый шаг и где произошла ошибка. Для знакомства с ZeroTrace доступны демоданные.</p></details>
        <details><summary>Что проверяет аудит?<Icon name="chevron"/></summary><p>Тайм-ауты и повторные попытки: сколько сервис ждёт ответа и сколько раз повторяет вызов. Например, аудит покажет, если повторные попытки не укладываются в доступное время ожидания, и объяснит расчёт.</p></details>
        <details><summary>Как проверить исправление?<Icon name="chevron"/></summary><p>На отдельном стенде задайте настройки и запустите тестовые запросы. Результаты покажут время ответа и ошибки. Изменения в рабочей системе применяются только после вашего подтверждения и при наличии разрешения сервера.</p></details>
      </div>
    </section></div>
    <div className="scroll-scene"><section className="scene-content landing-ending">

      <span className="landing-kicker">ПОПРОБУЙТЕ НА ГОТОВОМ ПРИМЕРЕ</span>
      <h2>Разберите задержку.<br/><em>Проверьте решение.</em></h2>
      <p>Пройдите весь путь в ZeroTrace: от запроса с задержкой до аудита настроек и тестового запуска. Начать можно без подключения своих сервисов.</p>
      <div className="landing-ending-actions"><LinkButton href="#/traces" icon="arrow" primary>Открыть ZeroTrace</LinkButton><LinkButton href="#/guide" icon="book">Как это работает</LinkButton></div>
      <div className="ending-route"><span>01 · Найти запрос</span><span>02 · Проверить настройки</span><span>03 · Запустить тест</span></div>
      <span className="ending-signature" aria-hidden="true">ZeroTrace.</span>
    </section></div>
    <footer className="landing-footer"><span>ZeroTrace</span><span>Трейсы, аудит и тестовый стенд — в одном месте.</span></footer>
  </div>;
}
