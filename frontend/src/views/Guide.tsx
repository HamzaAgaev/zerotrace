import sections from './guide-content.json';
import { PageHeader, Panel, LinkButton } from '../components/ui';

export function Guide() {
  return <div className="guide">
    <PageHeader title="Руководство пользователя" actions={<LinkButton href="#/traces" icon="arrow">К приложению</LinkButton>}/>
    <p className="guide-intro">Пошаговые сценарии ZeroTrace. Скриншоты актуального интерфейса сняты с демонстрационными данными в формате ноутбука 1440 × 900. Нажмите на изображение, чтобы открыть его в полном размере. Для реальной работы подключите сервер и отправку телеметрии.</p>
    <Panel title="Содержание"><nav className="guide-index" aria-label="Сценарии руководства">{sections.map(section=><a key={section.id} href={`#/guide?section=${section.id}`} onClick={event=>{event.preventDefault();document.getElementById(`guide-${section.id}`)?.scrollIntoView({block:'start'});}}>{section.title}</a>)}</nav></Panel>
    {sections.map(section=><section className="guide-section" id={`guide-${section.id}`} key={section.id}><h2>{section.title}</h2><ol>{section.steps.map(step=><li key={step}>{step}</li>)}</ol><a href={`guide/${section.image}`} target="_blank" rel="noreferrer" aria-label={`Открыть скриншот: ${section.title}`}><img src={`guide/${section.image}`} alt={section.title} loading="lazy" width="1440" height="900"/></a></section>)}
  </div>;
}
