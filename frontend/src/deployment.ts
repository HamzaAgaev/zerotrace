import type { Connection } from './api/types';

export const demoOnly = import.meta.env.VITE_DEMO_ONLY === 'true';
export const demoConnection = (): Connection => ({ mode: 'demo', baseUrl: '', environmentId: 'demo-shop' });
