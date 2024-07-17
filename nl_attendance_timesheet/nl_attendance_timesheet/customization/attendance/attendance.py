import frappe

def after_insert(self, method):
	if self.leave_type:
		is_wfh = frappe.db.get_value("Leave Type", self.leave_type, "custom_is_wfh")

		if is_wfh:
			self.custom_state = "Work From Home"

		if self.shift_type == "Weekly Off":
			self.custom_state = "Weekly Off"