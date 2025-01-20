import frappe
from frappe import _
from frappe.utils.data import get_datetime, nowdate
from datetime import datetime
from pypika import Criterion
from datetime import timedelta
from erpnext.accounts.utils import get_fiscal_year

current_date = nowdate()


@frappe.whitelist()
def generate_overtime_timesheets(start_date=current_date, end_date=current_date):
	SETTINGS_DOCTYPE = 'Navari Custom Payroll Settings'
	overtime_15 = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_15_activity')
	overtime_20 = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_20_activity')

	if not overtime_15 or not overtime_20:
		frappe.throw('Please set up both Overtime 1.5 and Overtime 2.0 activities in Navari Custom Payroll Settings')

	attendance = frappe.qb.DocType("Attendance")
	employee = frappe.qb.DocType("Employee")
	shift_type = frappe.qb.DocType("Shift Type")

	conditions = [attendance.docstatus == 1, attendance.status == "Present",
				  attendance.attendance_date[start_date:end_date]]

	query = frappe.qb.from_(attendance) \
		.inner_join(employee) \
		.on(employee.name == attendance.employee) \
		.left_join(shift_type) \
		.on(attendance.shift == shift_type.name) \
		.select(
		attendance.employee.as_("employee"),
		attendance.employee_name.as_("employee_name"),
		attendance.name.as_("name"),
		attendance.shift.as_("shift"),
		attendance.attendance_date.as_("attendance_date"),
		attendance.in_time.as_("in_time"),
		attendance.out_time.as_("out_time"),
		attendance.working_hours.as_("working_hours"),
		employee.company.as_("company"),
		employee.department.as_("department"),
		shift_type.start_time.as_("shift_start_time"),
		shift_type.end_time.as_("shift_end_time"),
		shift_type.min_hours_to_include_a_break,
		shift_type.unpaid_breaks_minutes.as_("unpaid_breaks_minutes"),
	).where(Criterion.all(conditions))

	attendance_records = query.run(as_dict=True)

	holiday_data = frappe._dict()
	fiscal_year = None

	for entry in attendance_records:
		if not holiday_data.get(entry.employee):
			date = entry.in_time.date() or entry.out_time.date()
			if not fiscal_year:
				fiscal_year = get_fiscal_year(date)[0]
			holiday_data[entry.employee] = frappe.db.get_value("Holiday List", {"custom_employee": entry.employee, "custom_fiscal_year": fiscal_year})
		if holiday_data.get(entry.employee):
			holiday_dates = frappe.db.get_all('Holiday', filters={'parent': holiday_data[entry.employee]}, pluck='holiday_date')
			if entry.attendance_date in holiday_dates:
				total_work_duration = calculate_holiday_hours(entry)
				if total_work_duration:
					create_new_timesheet(entry.employee, entry.employee_name, entry.company, entry.department,
										 overtime_20, entry.in_time, entry.working_hours, entry.name)
			else:
				from_time, hours = get_from_time_and_hours(entry)
				if from_time and hours:
					create_new_timesheet(entry.employee, entry.employee_name, entry.company, entry.department,
										 overtime_15, from_time, hours, entry.name)
		else:
			from_time, hours = get_from_time_and_hours(entry)
			if from_time and hours:
				create_new_timesheet(entry.employee, entry.employee_name, entry.company, entry.department, overtime_15,
									 from_time, hours, entry.name)
	
	frappe.msgprint(_("Timesheet generation has been completed."))

def calculate_holiday_hours(entry):
	if entry.out_time and entry.shift_start_time:
		in_time_dt = datetime.strptime(str(entry.in_time).split('.')[0], '%Y-%m-%d %H:%M:%S')
		out_time_dt = datetime.strptime(str(entry.out_time).split('.')[0], '%Y-%m-%d %H:%M:%S')
		shift_start_time_dt = datetime.combine(out_time_dt.date(), datetime.strptime(str(entry.shift_start_time), '%H:%M:%S').time())

		if in_time_dt < shift_start_time_dt:
			extra_hours = shift_start_time_dt.hour - in_time_dt.hour + (
						(shift_start_time_dt.minute - in_time_dt.minute) / 60) + (
									  (shift_start_time_dt.second - in_time_dt.second) / 3600)
			entry.working_hours -= extra_hours

		if entry.min_hours_to_include_a_break <= entry.working_hours:
			entry.working_hours -= entry.unpaid_breaks_minutes / 60

		total_work_duration = entry.working_hours
		return max(0, total_work_duration)

	return 0


def get_from_time_and_hours(entry):
	SETTINGS_DOCTYPE = 'Navari Custom Payroll Settings'
	if entry.out_time and entry.shift_end_time:
		check_in_time = entry.in_time.time()
		check_out_time = entry.out_time.time()
		shift_start_time = datetime.strptime(str(entry.shift_start_time), '%H:%M:%S').time()
		shift_end_time = datetime.strptime(str(entry.shift_end_time), '%H:%M:%S').time()
		overtime_threshold = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_threshold')

		if check_out_time > shift_end_time:
			overtime_minutes = ((check_out_time.hour - shift_end_time.hour) * 60) + (
						check_out_time.minute - shift_end_time.minute)

			if shift_start_time < check_in_time :
				overtime_minutes -= ((shift_start_time.hour - check_in_time.hour) * 60) + (
						max(check_in_time.minute, shift_start_time.minute) - min(check_in_time.minute, shift_start_time.minute))

			if overtime_minutes > overtime_threshold:

				"""convert datetime.timedelta to datetime.time"""
				shift_end_total_seconds = entry.shift_end_time.total_seconds()
				hours = int(shift_end_total_seconds // 3600)
				minutes = int((shift_end_total_seconds % 3600) // 60)
				seconds = int(shift_end_total_seconds % 60)
				shift_end = get_datetime(f"{hours}:{minutes}:{seconds}").time()

				attendnace_date = entry.attendance_date
				if attendnace_date != entry.out_time.date():
					attendnace_date = entry.attendance_date + timedelta(days = 1)

				from_time = datetime.combine(attendnace_date, shift_end)

				return from_time, overtime_minutes / 60
			else:
				"""Overtime is less than 30 minutes"""
				return None, None
		else:
			"""Check out time is not more than shift end time"""
			return None, None
	else:
		return None, None


def create_new_timesheet(employee, employee_name, company, department, overtime_type, from_time, hours, attendance):
	timesheet = frappe.new_doc('Timesheet')
	timesheet.employee = employee
	timesheet.company = company
	timesheet.department = department
	timesheet.attendance = attendance
	timesheet.employee_name = employee_name

	timesheet.append('time_logs', {
		'activity_type': overtime_type,
		'description': overtime_type,
		'from_time': from_time,
		'hours': hours,
		'completed': 1,
	})
	to_time = from_time + timedelta(hours = hours)
	args = frappe._dict({"from_time": from_time, "to_time": to_time})
	existing = timesheet.get_overlap_for("employee", args, employee)

	timesheet_with_attendance = frappe.db.get_value("Timesheet", {"attendance": attendance})

	emp_grade = frappe.db.get_value("Employee", employee, "grade")

	if frappe.get_cached_value("Department", department, "custom_timesheet_approval_required") and frappe.get_cached_value("Employee Grade", emp_grade, "custom_timesheet_approval_required"):
		return

	if existing or timesheet_with_attendance:
		return

	timesheet.insert(ignore_permissions=True, ignore_links=True, ignore_if_duplicate=True, ignore_mandatory=True)

	frappe.db.commit()
