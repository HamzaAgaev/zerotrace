import { Select } from './Select';
import { periods } from '../api/periodMetrics';
const labels: Record<number,string> = {5:'Последние 5 минут',30:'Последние 30 минут',60:'Последний час',180:'Последние 3 часа',1440:'Последние сутки'};
export function PeriodSelect({value,onChange}: {value:number;onChange:(value:number)=>void}) {
  return <Select aria-label="Период статистики" value={String(value)} onChange={value=>onChange(Number(value))}>
    {periods.map(period=><option key={period} value={String(period)}>{labels[period]}</option>)}
  </Select>;
}
