# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowtime, today

from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import add_serial_batch_ledgers
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry


class TestStockReconciliationBatchQty(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = (
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": "_Test Batch Count " + frappe.generate_hash(length=8),
					"abbr": frappe.generate_hash(length=5),
					"default_currency": "USD",
					"country": "United States",
					"create_chart_of_accounts_based_on": "Standard Template",
					"chart_of_accounts": "Standard",
				}
			)
			.insert()
			.name
		)
		cls.warehouse = (
			frappe.get_doc(
				{"doctype": "Warehouse", "warehouse_name": "_Test Batch Count", "company": cls.company}
			)
			.insert()
			.name
		)

	def setUp(self):
		super().setUp()
		self.item = make_item(properties={"has_batch_no": 1}).name
		self.batches = [
			frappe.get_doc({"doctype": "Batch", "item": self.item, "batch_id": frappe.generate_hash()})
			.insert()
			.name
			for _ in range(7)
		]
		self.parent = frappe._dict(
			doctype="Stock Reconciliation", company=self.company, posting_date=today(), posting_time=nowtime()
		)
		self.row = frappe._dict(
			name="new-stock-reconciliation-item",
			parenttype="Stock Reconciliation",
			item_code=self.item,
			warehouse=self.warehouse,
			type_of_transaction="Inward",
		)

	def test_create_zero_count_batches(self):
		entries = [{"batch_no": batch, "qty": 0} for batch in self.batches]
		bundle = add_serial_batch_ledgers(
			json.dumps(entries), json.dumps(self.row), json.dumps(self.parent), self.row.warehouse
		)
		bundle.reload()
		self.assertEqual([entry.qty for entry in bundle.entries], [0] * 7)
		self.assertEqual(bundle.total_qty, 0)

	def test_update_zero_count_batches(self):
		bundle = self.make_bundle([1] * 7)
		self.row.serial_and_batch_bundle = bundle.name
		bundle = self.make_bundle([0] * 7)
		bundle.reload()
		self.assertEqual([entry.qty for entry in bundle.entries], [0] * 7)
		self.assertEqual(bundle.total_qty, 0)

	def test_mixed_zero_and_positive_batch_counts(self):
		for quantities in ([0, "0", 2.5], [3.5, 0, "0"]):
			with self.subTest(quantities=quantities):
				bundle = self.make_bundle(quantities)
				bundle.reload()
				self.assertEqual([entry.qty for entry in bundle.entries], [float(qty) for qty in quantities])
				self.assertEqual(bundle.total_qty, sum(float(qty) for qty in quantities))
				self.row.serial_and_batch_bundle = bundle.name

	def test_missing_batch_quantity_keeps_default(self):
		bundle = self.make_bundle([None, ""])
		self.assertEqual([entry.qty for entry in bundle.entries], [1, 1])
		self.assertEqual(bundle.total_qty, 2)

	def test_other_vouchers_keep_batch_default(self):
		self.parent.doctype = self.row.parenttype = "Purchase Receipt"
		bundle = self.make_bundle([0])
		self.assertEqual(bundle.entries[0].qty, 1)
		self.assertEqual(bundle.total_qty, 1)

	def test_serial_entries_keep_default(self):
		self.row.item_code = make_item(properties={"has_serial_no": 1}).name
		serial_no = (
			frappe.get_doc(
				{"doctype": "Serial No", "item_code": self.row.item_code, "serial_no": frappe.generate_hash()}
			)
			.insert()
			.name
		)
		for qty in (None, 0, ""):
			with self.subTest(qty=qty):
				bundle = add_serial_batch_ledgers(
					[{"serial_no": serial_no, "qty": qty}], self.row, self.parent, self.row.warehouse
				)
				bundle.reload()
				self.assertEqual(bundle.entries[0].qty, 1)
				self.assertEqual(bundle.total_qty, 1)
				self.row.serial_and_batch_bundle = bundle.name

	def test_serial_and_batch_entries_keep_default(self):
		item = frappe.get_doc("Item", self.item)
		item.has_serial_no = 1
		item.save()
		serial_no = (
			frappe.get_doc(
				{
					"doctype": "Serial No",
					"item_code": self.item,
					"batch_no": self.batches[0],
					"serial_no": frappe.generate_hash(),
				}
			)
			.insert()
			.name
		)
		entries = [{"batch_no": self.batches[0], "serial_no": serial_no, "qty": 0}]
		for _ in range(2):
			bundle = add_serial_batch_ledgers(entries, self.row, self.parent, self.warehouse)
			bundle.reload()
			self.assertEqual(bundle.entries[0].qty, 1)
			self.assertEqual(bundle.total_qty, 1)
			self.row.serial_and_batch_bundle = bundle.name

	def test_zero_count_reconciliation_clears_batch_stock(self):
		make_stock_entry(
			item_code=self.item,
			target=self.warehouse,
			company=self.company,
			qty=10,
			rate=5,
			batch_no=self.batches[0],
			use_serial_batch_fields=0,
		)
		self.assertEqual(
			get_batch_qty(
				self.batches[0],
				self.warehouse,
				for_stock_levels=True,
				consider_negative_batches=True,
				ignore_reserved_stock=True,
			),
			10,
		)
		reconciliation = frappe.new_doc("Stock Reconciliation")
		reconciliation.update(self.parent)
		reconciliation.purpose = "Stock Reconciliation"
		reconciliation.posting_time = nowtime()
		reconciliation.set_posting_time = 1
		reconciliation.expense_account = frappe.get_cached_value(
			"Company", self.company, "stock_adjustment_account"
		)
		reconciliation.cost_center = frappe.get_cached_value("Company", self.company, "cost_center")
		row = reconciliation.append(
			"items", {"item_code": self.item, "warehouse": self.warehouse, "qty": 0, "valuation_rate": 5}
		)
		bundle = add_serial_batch_ledgers(
			[{"batch_no": self.batches[0], "qty": 0}],
			json.dumps(row.as_dict()),
			reconciliation,
			self.warehouse,
		)
		row.serial_and_batch_bundle = bundle.name
		reconciliation.insert()
		reconciliation.submit()
		self.assertEqual(reconciliation.items[0].qty, 0)
		self.assertEqual(
			get_batch_qty(
				self.batches[0],
				self.warehouse,
				for_stock_levels=True,
				consider_negative_batches=True,
				ignore_reserved_stock=True,
			),
			0,
		)

	def make_bundle(self, quantities):
		entries = [
			{"batch_no": batch, "qty": qty} for batch, qty in zip(self.batches, quantities, strict=False)
		]
		return add_serial_batch_ledgers(
			json.dumps(entries), json.dumps(self.row), json.dumps(self.parent), self.row.warehouse
		)
