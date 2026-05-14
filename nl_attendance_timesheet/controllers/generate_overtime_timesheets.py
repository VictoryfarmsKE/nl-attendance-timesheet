import frappe
from frappe import _
from frappe.utils.data import get_datetime, nowdate
from datetime import datetime, timedelta
from pypika import Criterion
from erpnext.accounts.utils import get_fiscal_year

SETTINGS_DOCTYPE = 'Navari Custom Payroll Settings'


@frappe.whitelist()
def generate_overtime_timesheets(start_date=None, end_date=None, shift_type=None):
    # frappe.log_error(f"Received request to generate overtime timesheets with parameters - Start Date: {start_date}, End Date: {end_date}, Shift Type: {shift_type}")
    if not start_date:
        start_date = nowdate()
    if not end_date:
        end_date = nowdate()

    if isinstance(shift_type, str):
        shift_type = frappe.parse_json(shift_type)
    shift_type = [s for s in (shift_type or []) if s]
    
    # frappe.log_error(f"Starting overtime timesheet generation with parameters - Start Date: {start_date}, End Date: {end_date}, Shift Type: {shift_type}")

    overtime_15 = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_15_activity')
    overtime_20 = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_20_activity')

    if not overtime_15 or not overtime_20:
        frappe.throw(_('Please set up both Overtime 1.5 and Overtime 2.0 activities in Navari Custom Payroll Settings'))

    attendance_records = _get_attendance_records(start_date, end_date, shift_type)

    # Cache holiday data and holiday dates per employee
    holiday_list_cache = frappe._dict()
    holiday_dates_cache = frappe._dict()
    fiscal_year = None

    for entry in attendance_records:
        grade = str(entry.get("grade") or "")
        grade_upper = grade.upper()
        if not (grade_upper.endswith("H") or 'P' in grade_upper):
            # frappe.log_error(f"[OT] Skipping {entry.employee} ({entry.name}): grade '{grade}' does not end with 'H' nor contain 'P'")
            continue

        employee = entry.employee

        if employee not in holiday_list_cache:
            record_date = None
            if entry.in_time:
                record_date = entry.in_time.date()
            elif entry.out_time:
                record_date = entry.out_time.date()

            if not fiscal_year and record_date:
                fiscal_year = get_fiscal_year(record_date)[0]

            holiday_list_cache[employee] = frappe.db.get_value(
                "Holiday List",
                {"custom_employee": employee, "custom_fiscal_year": fiscal_year}
            )

        holiday_list = holiday_list_cache.get(employee)

        if holiday_list:
            if holiday_list not in holiday_dates_cache:
                holiday_dates_cache[holiday_list] = frappe.db.get_all(
                    'Holiday',
                    filters={'parent': holiday_list},
                    pluck='holiday_date'
                )
            holiday_dates = holiday_dates_cache[holiday_list]

            if entry.attendance_date in holiday_dates:
                total_work_duration = calculate_holiday_hours(entry)
                if total_work_duration:
                    create_new_timesheet(
                        employee, entry.employee_name, entry.company, entry.department,
                        overtime_20, entry.in_time, entry.working_hours, entry.name
                    )
            else:
                _process_regular_overtime(entry, overtime_15)
        else:
            _process_regular_overtime(entry, overtime_15)

    frappe.msgprint(_("Timesheet generation has been completed."))


def _get_attendance_records(start_date, end_date, shift_type=None):
    """Build and run the attendance query."""
    attendance = frappe.qb.DocType("Attendance")
    employee = frappe.qb.DocType("Employee")
    shift_type_dt = frappe.qb.DocType("Shift Type")
    
    # frappe.log_error(f"Fetching attendance records with filters - Start Date: {start_date}, End Date: {end_date}, Shift Type: {shift_type}")

    conditions = [
        attendance.docstatus == 1,
        attendance.status == "Present",
        attendance.attendance_date[start_date:end_date],
        (employee.grade.like('%H') | employee.grade.like('%P%')),
        attendance.in_time.isnotnull(),
        attendance.out_time.isnotnull(),
    ]

    if shift_type:
        conditions.append(attendance.shift.isin(shift_type))

    return (
        frappe.qb.from_(attendance)
        .inner_join(employee).on(employee.name == attendance.employee)
        .left_join(shift_type_dt).on(attendance.shift == shift_type_dt.name)
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
            employee.grade.as_("grade"),
            shift_type_dt.start_time.as_("shift_start_time"),
            shift_type_dt.end_time.as_("shift_end_time"),
            shift_type_dt.min_hours_to_include_a_break,
            shift_type_dt.unpaid_breaks_minutes.as_("unpaid_breaks_minutes"),
        )
        .where(Criterion.all(conditions))
        .run(as_dict=True)
    )


def _process_regular_overtime(entry, overtime_15):
    """Helper to avoid duplicate regular overtime logic in the main loop."""
    from_time, hours = get_from_time_and_hours(entry)
    if from_time and hours:
        create_new_timesheet(
            entry.employee, entry.employee_name, entry.company, entry.department,
            overtime_15, from_time, hours, entry.name
        )


def calculate_holiday_hours(entry):
    if not (entry.out_time and entry.shift_start_time):
        return 0

    in_time_dt = datetime.strptime(str(entry.in_time).split('.')[0], '%Y-%m-%d %H:%M:%S')
    out_time_dt = datetime.strptime(str(entry.out_time).split('.')[0], '%Y-%m-%d %H:%M:%S')
    shift_start_dt = datetime.combine(
        out_time_dt.date(),
        datetime.strptime(str(entry.shift_start_time), '%H:%M:%S').time()
    )

    working_hours = entry.working_hours

    if in_time_dt < shift_start_dt:
        extra_hours = (
            (shift_start_dt.hour - in_time_dt.hour)
            + (shift_start_dt.minute - in_time_dt.minute) / 60
            + (shift_start_dt.second - in_time_dt.second) / 3600
        )
        working_hours -= extra_hours

    if entry.min_hours_to_include_a_break <= working_hours:
        working_hours -= entry.unpaid_breaks_minutes / 60

    return max(0, working_hours)


def _compute_overtime_minutes(check_in_time, check_out_time, shift_start_time, shift_end_time, crosses_midnight=False):
    """
    Calculate raw overtime minutes after shift end.
    Handles both same-day and midnight-crossing shifts.
    """
    if crosses_midnight:
        overtime_minutes = (((24 - shift_end_time.hour) + check_out_time.hour) * 60) + (
            check_out_time.minute - shift_end_time.minute
        )
    else:
        overtime_minutes = ((check_out_time.hour - shift_end_time.hour) * 60) + (
            check_out_time.minute - shift_end_time.minute
        )

    # Deduct late start if employee clocked in after shift start
    if shift_start_time < check_in_time:
        overtime_minutes -= (
            (shift_start_time.hour - check_in_time.hour) * 60
            + (max(check_in_time.minute, shift_start_time.minute) - min(check_in_time.minute, shift_start_time.minute))
        )

    return overtime_minutes


def _timedelta_to_time(td):
    """Convert a datetime.timedelta (e.g. from DB shift time fields) to datetime.time."""
    total_seconds = td.total_seconds()
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    return get_datetime(f"{hours}:{minutes}:{seconds}").time()


def get_from_time_and_hours(entry):
    missing = [f for f, v in [
        ('in_time', entry.in_time),
        ('out_time', entry.out_time),
        ('shift_end_time', entry.shift_end_time),
    ] if not v]

    if missing:
        frappe.logger().debug(
            f"Skipping overtime for attendance {entry.name} ({entry.employee}): "
            f"missing fields: {', '.join(missing)}"
        )
        return None, None

    overtime_threshold = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_threshold')

    check_in_time = entry.in_time.time()
    check_out_time = entry.out_time.time()
    shift_start_time = datetime.strptime(str(entry.shift_start_time), '%H:%M:%S').time()
    shift_end_time = datetime.strptime(str(entry.shift_end_time), '%H:%M:%S').time()

    same_day = entry.out_time.date() == entry.attendance_date
    crosses_midnight = not same_day

    if check_out_time > shift_end_time and same_day:
        overtime_minutes = _compute_overtime_minutes(
            check_in_time, check_out_time, shift_start_time, shift_end_time, crosses_midnight=False
        )
        attendance_date = entry.attendance_date

    elif check_out_time < shift_end_time and crosses_midnight:
        overtime_minutes = _compute_overtime_minutes(
            check_in_time, check_out_time, shift_start_time, shift_end_time, crosses_midnight=True
        )
        attendance_date = entry.attendance_date
    else:
        return None, None

    if overtime_minutes <= overtime_threshold:
        return None, None

    shift_end = _timedelta_to_time(entry.shift_end_time)
    from_time = datetime.combine(attendance_date, shift_end)
    return from_time, overtime_minutes / 60


def create_new_timesheet(employee, employee_name, company, department, overtime_type, from_time, hours, attendance, from_ot_req=False):
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

    to_time = from_time + timedelta(hours=hours)
    args = frappe._dict({"from_time": from_time, "to_time": to_time})
    existing = timesheet.get_overlap_for("employee", args, employee)
    timesheet_with_attendance = frappe.db.get_value("Timesheet", {"attendance": attendance})

    emp_grade = frappe.db.get_value("Employee", employee, "grade")
    overtime_20 = frappe.db.get_single_value(SETTINGS_DOCTYPE, 'overtime_20_activity')

    requires_approval = (
        not from_ot_req
        and overtime_type != overtime_20
        and frappe.get_cached_value("Department", department, "custom_timesheet_approval_required")
        and frappe.get_cached_value("Employee Grade", emp_grade, "custom_timesheet_approval_required")
    )

    if requires_approval or existing or timesheet_with_attendance:
        return

    timesheet.insert(
        ignore_permissions=True,
        ignore_links=True,
        ignore_if_duplicate=True,
        ignore_mandatory=True
    )
    frappe.db.commit()