import { Icon } from './ui';
export function SelectedFilters({groups}: {groups: {label:string;values:string[];name:(value:string)=>string;onChange:(values:string[])=>void}[]}) {
  if(!groups.some(group=>group.values.length))return null;
  return <div className="selected-filters" role="group" aria-label="Выбранные фильтры">
    {groups.filter(group=>group.values.length).map(group=><div className="selected-filter-group" key={group.label}>
      <span className="muted">{group.label}</span>
      {group.values.map(value=><span className="filter-chip" key={value}>
        <span>{group.name(value)}</span>
        <button type="button" aria-label={`Убрать ${group.label.toLowerCase()}: ${group.name(value)}`} onClick={()=>group.onChange(group.values.filter(item=>item!==value))}><Icon name="close" size={14}/></button>
      </span>)}
    </div>)}
  </div>;
}
