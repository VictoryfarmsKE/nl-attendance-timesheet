frappe.listview_settings["Attendance"] = {
    onload: function (list_view) {
		let me = this;
		list_view.page.add_inner_button(__("Record Absent Days"), function () {
            selected_values = list_view.get_checked_items();
            attendance = []
            console.log(selected_values)
            selected_values.forEach(element => {
                if (element.docstatus === 1){
                    console.log(element.name, element.docstatus)
                    attendance.push(element.name)
                }
            });
            if (attendance){
                frappe.call({
                    method:
                        "nl_attendance_timesheet.nl_attendance_timesheet.customization.attendance.attendance.create_additional_salary",
                    args: {
                        selected_values: attendance
                    },
                    freeze: true,
                    freeze_message: __("Creating record for absent leaves"),
                    callback: function (r) {
                        if (!r.exc) {
                            frappe.msgprint(__("Absent days updated successfully."))
                        }
                    },
                });
            }
        })
    }
}