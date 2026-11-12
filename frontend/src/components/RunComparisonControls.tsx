import type { ReactNode } from 'react';
import { Select } from './Select';
import { Field } from './ui';

type RunOption = { id: string; label: string };

export function RunComparisonControls({
  options, baseline, candidate, onBaselineChange, onCandidateChange, allowClear = false, children,
}: {
  options: RunOption[];
  baseline: string;
  candidate: string;
  onBaselineChange: (value: string) => void;
  onCandidateChange: (value: string) => void;
  allowClear?: boolean;
  children?: ReactNode;
}) {
  return <div className="form-grid run-comparison-controls">
    {[
      { label: 'До изменения', value: baseline, onChange: onBaselineChange },
      { label: 'После изменения', value: candidate, onChange: onCandidateChange },
    ].map(({ label, value, onChange }) => <Field key={label} label={label}>
      <Select aria-label={label} value={value} onChange={onChange} disabled={!options.length}>
        <option value="" disabled={!allowClear}>Выберите тест</option>
        {options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
      </Select>
    </Field>)}
    {children}
  </div>;
}
