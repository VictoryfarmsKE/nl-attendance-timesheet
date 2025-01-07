# Copyright (c) 2023, Navari Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class NavariCustomPayrollSettings(Document):
	def validate(self):
		for row in self.new_bonus_vs_score_matrix:
			row.lower_limit = row.lower_limit_score * 20
			row.upper_limit = row.upper_limit_score * 20
			row.attained_score = row.attained_result_score