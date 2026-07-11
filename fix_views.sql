INSERT INTO ir_act_window_view (sequence, view_id, act_window_id, view_mode)
SELECT 1, id, 277, 'list' FROM ir_ui_view WHERE name = 'crm.lead.list.lead' LIMIT 1;
INSERT INTO ir_act_window_view (sequence, view_id, act_window_id, view_mode)
SELECT 2, id, 277, 'kanban' FROM ir_ui_view WHERE name = 'crm.lead.kanban' LIMIT 1;
INSERT INTO ir_act_window_view (sequence, view_id, act_window_id, view_mode)
SELECT 3, id, 277, 'calendar' FROM ir_ui_view WHERE name = 'crm.lead.calendar.lead' LIMIT 1;
INSERT INTO ir_act_window_view (sequence, view_id, act_window_id, view_mode)
SELECT 4, id, 277, 'pivot' FROM ir_ui_view WHERE name = 'crm.lead.view.pivot' LIMIT 1;
INSERT INTO ir_act_window_view (sequence, view_id, act_window_id, view_mode)
SELECT 5, id, 277, 'graph' FROM ir_ui_view WHERE name = 'crm.lead.view.graph' LIMIT 1;
