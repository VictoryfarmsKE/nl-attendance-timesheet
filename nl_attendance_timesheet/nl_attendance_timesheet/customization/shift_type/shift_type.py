import inspect
import itertools
import json
import frappe
from frappe.utils import cint, create_batch, flt
from hrms.hr.doctype.shift_type.shift_type import ShiftType
from hrms.hr.doctype.employee_checkin.employee_checkin import (
	mark_attendance_and_link_log,
)

EMPLOYEE_CHUNK_SIZE = 50

class CustomShiftType(ShiftType):
	@frappe.whitelist()
	def process_auto_attendance(self, is_manually_triggered=False):
		if (
			not cint(self.enable_auto_attendance)
			or not self.process_attendance_after
			or not self.last_sync_of_checkin
		):
			return

		logs = get_employee_checkins(self)

		for key, group in itertools.groupby(logs, key=lambda x: (x["employee"], x["shift_start"])):
			single_shift_logs = list(group)
			attendance_date = key[1].date()
			employee = key[0]

			if not self.should_mark_attendance(employee, attendance_date):
				continue

			working_hours_threshold_for_half_day = flt(self.working_hours_threshold_for_half_day)
			working_hours_threshold_for_absent = flt(self.working_hours_threshold_for_absent)

			if hasattr(self, "is_half_holiday") and self.is_half_holiday(employee, attendance_date):
				working_hours_threshold_for_half_day = flt(self.working_hours_threshold_for_half_day) / 2
				working_hours_threshold_for_absent = flt(self.working_hours_threshold_for_absent) / 2

			_get_attendance_params = inspect.signature(ShiftType.get_attendance).parameters
			if len(_get_attendance_params) > 2:
				# hrms v16+: get_attendance(logs, threshold_absent, threshold_half_day)
				(
					attendance_status,
					working_hours,
					late_entry,
					early_exit,
					in_time,
					out_time,
				) = self.get_attendance(single_shift_logs, working_hours_threshold_for_absent, working_hours_threshold_for_half_day)
			else:
				# hrms v15: get_attendance(logs)
				(
					attendance_status,
					working_hours,
					late_entry,
					early_exit,
					in_time,
					out_time,
				) = self.get_attendance(single_shift_logs)

			#customization for getting shift
			employee_shift = self.name

			if in_time:
				assigned_shift = frappe.db.sql(f"""
						SELECT
							DISTINCT shift_type
						FROM
							`tabShift Assignment`
						WHERE
							docstatus = 1
							AND status = "Active"
							AND employee = '{employee}'
							AND start_date <= '{in_time.date()}'
							AND (end_date is NULL or end_date = '' or end_date >= '{in_time.date()}')
					""")

				if assigned_shift:
					employee_shift = assigned_shift[0][0]

			mark_attendance_and_link_log(
				single_shift_logs,
				attendance_status,
				attendance_date,
				working_hours,
				late_entry,
				early_exit,
				in_time,
				out_time,
				employee_shift,
			)

		if not frappe.flags.in_test:
			frappe.db.commit() 

		assigned_employees = self.get_assigned_employees(self.process_attendance_after, True)

		for batch in create_batch(assigned_employees, EMPLOYEE_CHUNK_SIZE):
			for employee in batch:
				self.mark_absent_for_dates_with_no_attendance(employee)
				if hasattr(self, "mark_absent_for_half_day_dates"):
					self.mark_absent_for_half_day_dates(employee)

			if not frappe.flags.in_test:
				frappe.db.commit()  
   
def get_employee_checkins(self):
	filters = {
		"skip_auto_attendance": 0,
		"attendance": ("is", "not set"),
		"time": (">=", self.process_attendance_after),
		"shift_actual_end": ("<", self.last_sync_of_checkin),
		"shift": self.name,
	}

	# v16+: filter out off-shift check-ins
	if frappe.db.has_column("Employee Checkin", "offshift"):
		filters["offshift"] = 0

	allowed_grades = frappe.db.get_all("Employee Grade", {"custom_allow_mark_attendance": 1}, pluck = "name")

	if allowed_grades:
		filters["grade"] = ["in", allowed_grades]

	return frappe.get_all(
		"Employee Checkin",
		fields=[
			"name",
			"employee",
			"log_type",
			"time",
			"shift",
			"shift_start",
			"shift_end",
			"shift_actual_start",
			"shift_actual_end",
			"device_id",
		],
		filters=filters,
		order_by="employee,time",
	)

@frappe.whitelist()
def mark_selected_attendance(selected_values):
	if isinstance(selected_values, str):
		selected_values = json.loads(selected_values)

	for row in selected_values:
		doc = frappe.get_doc("Shift Type", row)
		try:
			doc.process_auto_attendance()
		except:
			return {"status": False, "message": f"Getting error while marking attendance for {doc.name}"}
	
	return {"status": True, "message": "Attendacne Marked successfully"}