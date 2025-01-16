import frappe
from frappe import _
from ..controllers.get_employee_attendance import get_employee_attendance, get_employee_overtime_attendance
from erpnext.accounts.utils import get_fiscal_year

SETTINGS_DOCTYPE = 'Navari Custom Payroll Settings'

maximum_monthly_hours = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'maximum_monthly_hours')
maximum_billable_hours = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'maximum_billable_hours')
overtime_15 = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_15_activity')
overtime_20 = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_20_activity')

@frappe.whitelist()
def add_attendance_data(payroll_entry):
	salary_slips = frappe.db.get_all('Salary Slip', filters = { 'payroll_entry': payroll_entry, 'docstatus': 0 })

	leave_type_data = frappe._dict()

	shift_data = frappe._dict()

	for entry in salary_slips:
		salary_slip = frappe.get_doc('Salary Slip', entry.get('name'))
		salary_slip.attendance = []
		salary_slip.regular_overtime = []
		salary_slip.holiday_overtime = []

		salary_slip.regular_working_hours = 0
		salary_slip.overtime_hours = 0
		salary_slip.holiday_hours = 0

		attendance = get_employee_attendance(salary_slip.get('employee'), salary_slip.get('start_date'), salary_slip.get('end_date'))
		holiday_dates = get_holiday_dates(salary_slip.get('employee'), salary_slip.end_date)

		attendance_list = []

		if attendance:
			for attendance_entry in attendance:
				attendance_list.append(attendance_entry.get("name"))
				if attendance_entry.get('attendance_date') not in (holiday_dates or []) and attendance_entry.get('working_hours') > 0:
					billiable_hours = 0

					if not attendance_entry.get('include_unpaid_breaks'):
						billiable_hours = attendance_entry.get('payment_hours')
					else:
						if attendance_entry.get('working_hours') > attendance_entry.get('min_hours_to_include_a_break'):
							billiable_hours = attendance_entry.get('working_hours') - (attendance_entry.get('unpaid_breaks_minutes') / 60)
						else:
							billiable_hours = attendance_entry.get('working_hours')
					if not shift_data.get(attendance_entry.get("shift")):
						if attendance_entry.shift_start_time < attendance_entry.shift_end_time:
							shift_data[attendance_entry.get("shift")] = (((attendance_entry.shift_end_time - attendance_entry.shift_start_time).seconds / 3600) - (attendance_entry.get("unpaid_breaks_minutes", 0) / 60)) or maximum_billable_hours
						else:
							shift_data[attendance_entry.get("shift")] = ((24 - ((attendance_entry.shift_start_time - attendance_entry.shift_end_time).seconds / 3600) - (attendance_entry.get("unpaid_breaks_minutes", 0) / 60))) or maximum_billable_hours

					maximum_billable_hours = shift_data.get(attendance_entry.get("shift"))
					actial_billable_hours = min(maximum_billable_hours, billiable_hours)

					salary_slip.append('attendance', {
						'attendance_date': attendance_entry.get('attendance_date'),
						'hours_worked': attendance_entry.get('working_hours'),
						'include_unpaid_breaks': attendance_entry.get('include_unpaid_breaks'),
						'unpaid_breaks_minutes': attendance_entry.get('unpaid_breaks_minutes'),
						'min_hours_to_include_a_break': attendance_entry.get('min_hours_to_include_a_break'),
						'billiable_hours': actial_billable_hours
					})

					salary_slip.regular_working_hours += actial_billable_hours

		leave_applications_data = frappe.db.get_all("Leave Application", {"employee": salary_slip.employee, "from_date": [">=", salary_slip.start_date], "to_date": ["<=", salary_slip.end_date], "docstatus": 1, "status": "Approved"}, ["leave_type", "total_leave_days"])

		employee_grade = frappe.db.get_value("Employee", salary_slip.employee, "grade")

		if not employee_grade:
			frappe.throw(_("Grade is not mentioed in to {0}").format(salary_slip.employee))

		for row in leave_applications_data:
			if not leave_type_data.get(row.leave_type):
				leave_type_data[row.leave_type] = frappe._dict({"hours_to_be_added": 0, "allowed_grades": []})
				leave_type_data[row.leave_type]["hours_to_be_added"] = frappe.db.get_value("Leave Type", row.leave_type, "custom_working_hours")
				leave_type_data[row.leave_type]["allowed_grades"] = frappe.db.get_all("Leave Grade", {"parent": row.leave_type, "parentfield": "custom_working_grade"}, pluck = "employee_grade")

			if employee_grade in leave_type_data[row.leave_type]["allowed_grades"]:
				salary_slip.regular_working_hours += leave_type_data[row.leave_type]["hours_to_be_added"] * row.total_leave_days

		overtime_attendance = get_employee_overtime_attendance(salary_slip.get('employee'), salary_slip.get('start_date'), salary_slip.get('end_date'))

		if overtime_attendance:
			for overtime_attendance_record in overtime_attendance:
				if overtime_attendance_record.get('activity_type') == overtime_15:
					salary_slip.append('regular_overtime', {
						'timesheet': overtime_attendance_record.get('name'),
						'hours': overtime_attendance_record.get('total_hours')
					})
					salary_slip.overtime_hours += overtime_attendance_record.get('total_hours')

				if overtime_attendance_record.get('activity_type') == overtime_20:
					salary_slip.append('holiday_overtime', {
						'timesheet': overtime_attendance_record.get('name'),
						'hours': overtime_attendance_record.get('total_hours')
					})
					salary_slip.holiday_hours += overtime_attendance_record.get('total_hours')

		if salary_slip.regular_working_hours > maximum_monthly_hours:
			salary_slip.overtime_hours += salary_slip.regular_working_hours - maximum_monthly_hours
			salary_slip.regular_working_hours = maximum_monthly_hours
		elif salary_slip.regular_working_hours < maximum_monthly_hours:
			balance_to_maximum_monthly_hours = maximum_monthly_hours - salary_slip.regular_working_hours
			if salary_slip.overtime_hours <= balance_to_maximum_monthly_hours:
				salary_slip.regular_working_hours += salary_slip.overtime_hours
				salary_slip.overtime_hours = 0
			else:
				salary_slip.overtime_hours -= balance_to_maximum_monthly_hours
				salary_slip.regular_working_hours += balance_to_maximum_monthly_hours



		if salary_slip.attendance or salary_slip.regular_overtime or salary_slip.holiday_overtime:
			salary_slip.save(ignore_permissions=True)
			frappe.db.commit()

def get_holiday_dates(employee, end_date):
	fiscal_year = get_fiscal_year(end_date)[0]
	holiday_list = frappe.db.get_value('Holiday List', {"custom_employee": employee, "custom_fiscal_year": fiscal_year}, 'name')
	if holiday_list:
		dates = frappe.db.get_all('Holiday', filters = { 'parent': holiday_list }, pluck = 'holiday_date')
		return dates
	return None