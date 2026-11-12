import { readFile, writeFile, access } from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const sections = JSON.parse(await readFile(new URL('src/views/guide-content.json', root), 'utf8'));
const target = new URL('../docs/users/README.md', root);
const text = '# Руководство пользователя ZeroTrace\n\nПошаговые сценарии текущего приложения. Иллюстрации обновлены 13 ноября 2026 года после редизайна. Скриншоты сняты в формате ноутбука 1440 × 900, без масштабирования и ручной обрезки, с демонстрационными данными; HTTP-нагрузка в этом режиме не выполняется. Это же руководство доступно в приложении по ссылке `#/guide`.\n\n' + sections.map(s => `## ${s.title}\n\n${s.steps.map((step, i) => `${i + 1}. ${step}`).join('\n')}\n\n![${s.title}](../../frontend/public/guide/${s.image})\n`).join('\n') + '\n## Дополнительные экраны\n\n![Статистика системы](../../frontend/public/guide/system-statistics.jpg)\n\n![План изменения](../../frontend/public/guide/plan.jpg)\n\n![История изменений](../../frontend/public/guide/history.jpg)\n\n[Подключение OTLP](../guides/operations.md) · [Формат JSON-сценария](../guides/lab-scenario.md) · [Документация разработчика](../developers/README.md)\n';
if (process.argv.includes('--check')) {
  await Promise.all([...sections.map(section => section.image), 'system-statistics.jpg', 'plan.jpg', 'history.jpg'].map(image => access(new URL(`public/guide/${image}`, root))));
  if (await readFile(target, 'utf8') !== text) throw new Error('Руководство устарело: npm run docs');
} else await writeFile(target, text);
