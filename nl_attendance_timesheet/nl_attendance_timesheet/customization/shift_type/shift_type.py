import itertools

import frappe
from frappe.utils import cint, create_batch
from hrms.hr.doctype.shift_type.shift_type import ShiftType
from hrms.hr.doctype.employee_checkin.employee_checkin import (
	mark_attendance_and_link_log,
)

EMPLOYEE_CHUNK_SIZE = 50

class CustomShiftType(ShiftType):
	@frappe.whitelist()
	def process_auto_attendance(self):
		if (
			not cint(self.enable_auto_attendance)
			or not self.process_attendance_after
			or not self.last_sync_of_checkin
		):
			return

		logs = self.get_employee_checkins()

		for key, group in itertools.groupby(logs, key=lambda x: (x["employee"], x["shift_start"])):
			single_shift_logs = list(group)
			attendance_date = key[1].date()
			employee = key[0]

			if not self.should_mark_attendance(employee, attendance_date):
				continue

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

		# commit after processing checkin logs to avoid losing progress
		frappe.db.commit()  # nosemgrep

		assigned_employees = self.get_assigned_employees(self.process_attendance_after, True)

		# mark absent in batches & commit to avoid losing progress since this tries to process remaining attendance
		# right from "Process Attendance After" to "Last Sync of Checkin"
		for batch in create_batch(assigned_employees, EMPLOYEE_CHUNK_SIZE):
			for employee in batch:
				self.mark_absent_for_dates_with_no_attendance(employee)

			frappe.db.commit()  # nosemgrep