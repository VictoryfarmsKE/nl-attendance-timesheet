frappe.listview_settings["Shift Type"] = {
    onload: function (list_view) {
        let me = this;
        list_view.page.add_inner_button(__("Mark Attendance"), function () {
            selected_values = list_view.get_checked_items();
            shift_types = []
            selected_values.forEach(element => {
                shift_types.push(element.name)
            });
            if (shift_types){
                frappe.call({
                    method:
                        "nl_attendance_timesheet.nl_attendance_timesheet.customization.shift_type.shift_type.mark_selected_attendance",
                    args: {
                        selected_values: shift_types
                    },
                    freeze: true,
                    freeze_message: __("Marking Attendance"),
                    callback: function (r) {
                        if (!r.exc) {
                            frappe.msgprint(__("Attendance maked successfully"))
                        }
                    },
                });
            }
        })
    }
}