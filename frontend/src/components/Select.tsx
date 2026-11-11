import { Children, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from './ui';

interface Props {
  children: ReactNode;
  value: string;
  onChange: (value: string) => void;
  'aria-label'?: string;
  disabled?: boolean;
}

interface Option {
  value: string;
  label: string;
  content: ReactNode;
  disabled: boolean;
}

export function Select({ children, value, onChange, disabled, 'aria-label': label }: Props) {
  const options: Option[] = Children.toArray(children).filter(isValidElement).map(child => {
    const props = child.props as { value?: string; label?: string; disabled?: boolean; children: ReactNode };
    const label = props.label ?? Children.toArray(props.children).join('');
    return { value: props.value ?? label, label, content: props.label === undefined ? label : props.children, disabled: !!props.disabled };
  });
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState<CSSProperties>({});
  const search = useRef({ text: '', time: 0 });
  const selected = options.findIndex(option => option.value === value);
  const enabled = options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0);

  function show(last = false) {
    if (disabled || !enabled.length) return;
    setActive(selected >= 0 && !options[selected].disabled ? selected : enabled[last ? enabled.length - 1 : 0]);
    setPosition({});
    setOpen(true);
  }

  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    trigger.current?.focus();
  }

  useLayoutEffect(() => {
    if (!open || !trigger.current || !popup.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const upwards = below < 180 && above > below;
    const width = Math.min(Math.max(rect.width, popup.current.getBoundingClientRect().width), window.innerWidth - 24);
    setPosition({
      position: 'fixed', width,
      left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
      ...(upwards ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
      maxHeight: Math.min(window.innerHeight * .55, upwards ? above : below),
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      const target = event.target as Node;
      if (!trigger.current?.contains(target) && !popup.current?.contains(target)) setOpen(false);
    }
    const close = () => setOpen(false);
    function onScroll(event: Event) {
      if (!popup.current?.contains(event.target as Node)) close();
    }
    document.addEventListener('pointerdown', outside);
    window.addEventListener('resize', close);
    document.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('resize', close);
      document.removeEventListener('scroll', onScroll, true);
    };
  }, [open]);

  useEffect(() => {
    const menu = popup.current;
    const option = document.getElementById(`${id}-option-${active}`);
    if (!open || !menu || !option) return;
    const top = option.offsetTop;
    const bottom = top + option.offsetHeight;
    if (top < menu.scrollTop) menu.scrollTop = top;
    else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight;
  }, [open, active, id]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (!open) {
        show(event.key === 'ArrowUp' || event.key === 'End');
        if (event.key === 'Home') setActive(enabled[0]);
        if (event.key === 'End') setActive(enabled[enabled.length - 1]);
        return;
      }
      const index = enabled.indexOf(active);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length;
      if (enabled.length) setActive(enabled[next]);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (open) choose(active);
      else show();
    } else if (event.key === 'Escape') {
      if (open) { event.preventDefault(); event.stopPropagation(); }
      setOpen(false);
    } else if (event.key === 'Tab') {
      setOpen(false);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      const text = (now - search.current.time < 700 ? search.current.text : '') + event.key.toLocaleLowerCase();
      search.current = { text, time: now };
      const index = options.findIndex(option => !option.disabled && option.label.toLocaleLowerCase().startsWith(text));
      if (index >= 0) {
        if (!open) show();
        setActive(index);
      }
    }
  }

  return <>
    <button ref={trigger} className="select-control" type="button" role="combobox"
      aria-label={label} aria-expanded={open} aria-haspopup="listbox"
      aria-controls={open ? id : undefined} aria-activedescendant={open ? `${id}-option-${active}` : undefined}
      disabled={disabled || !enabled.length} onClick={() => open ? setOpen(false) : show()}
      onKeyDown={onKeyDown} onBlur={() => setOpen(false)}>
      <span>{options[selected]?.content ?? 'Выберите значение'}</span><Icon name="chevron" size={14}/>
    </button>
    {open && createPortal(<div ref={popup} id={id} role="listbox" aria-label={label ?? 'Варианты выбора'}
      className="select-menu" style={{ position: 'fixed', ...position }}>
      {options.map((option, index) => <div key={option.value} id={`${id}-option-${index}`}
        role="option" aria-selected={option.value === value} aria-disabled={option.disabled || undefined}
        className={`select-option ${active === index ? 'is-active' : ''}`}
        onPointerMove={() => { if (!option.disabled) setActive(index); }}
        onMouseDown={event => event.preventDefault()} onClick={() => choose(index)}>
        <span className="select-check">{option.value === value && <Icon name="check" size={14}/>}</span>
        <span>{option.content}</span>
      </div>)}
    </div>, document.body)}
  </>;
}
