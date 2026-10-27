/** napari's Labels tool state of an editable Labels layer, as the worker reports it (env_worker/scientific_labels_tool.py). */
export const labelsModes = ['pan_zoom', 'transform', 'paint', 'erase', 'fill', 'pick', 'polygon'] as const;
export type LabelsTool = { mode: typeof labelsModes[number]; selectedLabel: string; background: string; brushSize: number;
  preserve: boolean; contiguous: boolean; editDimensions: number; showSelected: boolean };
/** napari's Labels key-binding actions the Studio may run (their napari default keys are in scientific-labels.ts). */
export type LabelsAction = 'activate_labels_erase_mode' | 'activate_labels_paint_mode' | 'activate_labels_polygon_mode' |
  'activate_labels_fill_mode' | 'activate_labels_picker_mode' | 'activate_labels_pan_zoom_mode' | 'new_label' |
  'swap_selected_and_background_labels' | 'decrease_label_id' | 'increase_label_id' | 'decrease_brush_size' |
  'increase_brush_size' | 'toggle_preserve_labels';
export type LabelsToolSettings = Partial<Pick<LabelsTool, 'mode' | 'selectedLabel' | 'brushSize' | 'preserve' | 'contiguous' | 'editDimensions' | 'showSelected'>>;
export type LabelsToolChange = { operation: 'labels-tool'; value: { layer: string; action: LabelsAction } | { layer: string; settings: LabelsToolSettings } };

const label = (value: unknown) => typeof value === 'string' && /^(0|[1-9][0-9]{0,19})$/.test(value);
export function labelsTool(raw: unknown): LabelsTool {
  const row = raw as Record<string, unknown>;
  if (!row || typeof row !== 'object' || Array.isArray(row) ||
      Object.keys(row).sort().join() !== ['background','brushSize','contiguous','editDimensions','mode','preserve','selectedLabel','showSelected'].join() ||
      !labelsModes.includes(row.mode as LabelsTool['mode']) || !label(row.selectedLabel) || !label(row.background) ||
      !Number.isInteger(row.brushSize) || Number(row.brushSize) < 1 || Number(row.brushSize) > (1 << 20) ||
      !Number.isInteger(row.editDimensions) || Number(row.editDimensions) < 1 || Number(row.editDimensions) > 32 ||
      ['preserve','contiguous','showSelected'].some(key => typeof row[key] !== 'boolean'))
    throw new Error('The Labels tool state is not supported. Keep the original and reopen the session.');
  return row as LabelsTool;
}
