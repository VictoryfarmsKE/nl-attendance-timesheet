import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
	"""
	Enforce that Overtime activity rows can only have their hours reduced, never increased.
	"""
	if doc.is_new() and not doc.amended_from:
		return

	for row in doc.time_logs:
		if not row.activity_type or "overtime" not in row.activity_type.lower():
			continue

		original_hours = _get_original_hours(doc, row)

		if original_hours is None:
			continue

		#if flt(row.hours) > flt(original_hours)
		if flt(row.hours, 2) > flt(original_hours, 2)::
			frappe.throw(
				_("Row {0}: Overtime hours cannot be increased. "
				  "Original: <b>{1}h</b>  Attempted: <b>{2}h</b>. "
				  "Only reductions are permitted for Overtime activities.").format(
					row.idx,
					flt(original_hours, 2),
					flt(row.hours, 2),
				),
				title=_("Overtime Hours Increase Not Allowed"),
			)


def _get_original_hours(doc, row):
	"""
	Return the authoritative original hours for a time log row, or None if not applicable.
	"""
	if not doc.is_new():
		return frappe.db.get_value("Timesheet Detail", row.name, "hours")

	if doc.amended_from:
		return frappe.db.get_value(
			"Timesheet Detail",
			{
				"parent": doc.amended_from,
				"activity_type": row.activity_type,
				"from_time": row.from_time,
			},
			"hours",
		)

	return None
