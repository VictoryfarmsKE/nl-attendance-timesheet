import frappe
from frappe.utils import flt, get_last_day
import json

def after_insert(self, method):
	if self.leave_type:
		is_wfh = frappe.db.get_value("Leave Type", self.leave_type, "custom_is_wfh")

		if is_wfh:
			self.custom_state = "Work From Home"

		if self.get("shift") and self.shift == "Weekly Off":
			self.custom_state = "Weekly Off"

@frappe.whitelist()
def create_additional_salary(selected_values):

	if isinstance(selected_values, str):
		selected_values = json.loads(selected_values)

	for row in selected_values:
		employee, attendance_date = frappe.db.get_value("Attendance", row, ["employee", "attendance_date"])

		gross_pay = frappe.db.get_value("Employee", employee, "ctc")

		daily_pay = gross_pay / 30

		ads_doc = frappe.new_doc("Additional Salary")
		ads_doc.employee = employee
		ads_doc.salary_component = "Absent Days"
		ads_doc.payroll_date = get_last_day(attendance_date)
		ads_doc.amount = flt(daily_pay, 2)
		ads_doc.ref_doctype = "Attendance"
		ads_doc.ref_docname = row

		ads_doc.save()

@frappe.whitelist()
def update_holiday_list(selected_values):
	if isinstance(selected_values, str):
		selected_values = json.loads(selected_values)

	for row in selected_values:
		doc = frappe.get_doc("Holiday List", row)
		doc.get_weekly_off_dates()
		doc.save()