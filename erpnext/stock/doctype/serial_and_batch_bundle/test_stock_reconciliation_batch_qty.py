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

	def test_zero_batch_count_still_requires_serial_number_for_serial_item(self):
		item = frappe.get_doc("Item", self.item)
		item.has_serial_no = 1
		item.save()
		with self.assertRaises(frappe.ValidationError):
			add_serial_batch_ledgers(
				json.dumps([{"batch_no": self.batches[0], "qty": 0}]),
				json.dumps(self.row),
				json.dumps(self.parent),
				self.warehouse,
			)

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

	def test_zero_counts_with_empty_batches_save_submit_and_cancel(self):
		initial_quantities = [10, 0, 6, 0, 0, 4, 0]
		unselected_batch = (
			frappe.get_doc({"doctype": "Batch", "item": self.item, "batch_id": frappe.generate_hash()})
			.insert()
			.name
		)
		for batch, qty in zip([*self.batches, unselected_batch], [*initial_quantities, 9], strict=True):
			if qty:
				make_stock_entry(
					item_code=self.item,
					target=self.warehouse,
					company=self.company,
					qty=qty,
					rate=5,
					batch_no=batch,
					use_serial_batch_fields=0,
				)

		self.assert_batch_balances([*self.batches, unselected_batch], [*initial_quantities, 9])
		reconciliation = self.make_reconciliation([0] * 7)
		reconciliation.insert()
		current_bundle = reconciliation.items[0].current_serial_and_batch_bundle
		self.assertTrue(current_bundle)
		reconciliation.save()
		reconciliation.reload()
		self.assertEqual(reconciliation.items[0].current_serial_and_batch_bundle, current_bundle)
		self.assertEqual(reconciliation.items[0].current_qty, 20)
		self.assert_batch_balances([*self.batches, unselected_batch], [*initial_quantities, 9])
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": self.warehouse}, "actual_qty"),
			29,
		)

		reconciliation.submit()
		reconciliation.reload()
		self.assertEqual(reconciliation.docstatus, 1)
		self.assertEqual(reconciliation.items[0].qty, 0)
		self.assert_batch_balances([*self.batches, unselected_batch], [0] * 7 + [9])
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": self.warehouse}, "actual_qty"),
			9,
		)

		reconciliation.cancel()
		reconciliation.reload()
		self.assertEqual(reconciliation.docstatus, 2)
		self.assert_batch_balances([*self.batches, unselected_batch], [*initial_quantities, 9])
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": self.warehouse}, "actual_qty"),
			29,
		)

	def test_positive_count_for_empty_batch_uses_zero_current_balance(self):
		make_stock_entry(
			item_code=self.item,
			target=self.warehouse,
			company=self.company,
			qty=10,
			rate=5,
			batch_no=self.batches[0],
			use_serial_batch_fields=0,
		)
		reconciliation = self.make_reconciliation([0, 2, 0, 0, 0, 0, 0])
		reconciliation.insert()
		self.assertEqual(reconciliation.items[0].current_qty, 10)
		current_bundle = frappe.get_doc(
			"Serial and Batch Bundle", reconciliation.items[0].current_serial_and_batch_bundle
		)
		self.assertEqual([entry.qty for entry in current_bundle.entries], [-10, 0, 0, 0, 0, 0, 0])
		reconciliation.submit()
		self.assert_batch_balances(self.batches, [0, 2, 0, 0, 0, 0, 0])
		reconciliation.cancel()
		self.assert_batch_balances(self.batches, [10, 0, 0, 0, 0, 0, 0])

	def test_other_outward_bundles_still_reject_zero_batch_quantity(self):
		for voucher_type, has_serial_no in [("Stock Entry", 0), ("Stock Reconciliation", 1)]:
			with self.subTest(voucher_type=voucher_type, has_serial_no=has_serial_no):
				bundle = frappe.get_doc(
					{
						"doctype": "Serial and Batch Bundle",
						"voucher_type": voucher_type,
						"type_of_transaction": "Outward",
						"item_code": self.item,
						"has_batch_no": 1,
						"has_serial_no": has_serial_no,
						"entries": [{"batch_no": self.batches[0], "qty": 0}],
					}
				)
				with self.assertRaises(frappe.ValidationError):
					bundle.validate_serial_and_batch_no()

	def make_reconciliation(self, quantities):
		reconciliation = frappe.new_doc("Stock Reconciliation")
		reconciliation.update(self.parent)
		reconciliation.purpose = "Stock Reconciliation"
		reconciliation.posting_time = nowtime()
		reconciliation.set_posting_time = 1
		row = reconciliation.append(
			"items",
			{
				"item_code": self.item,
				"warehouse": self.warehouse,
				"qty": sum(quantities),
				"valuation_rate": 5,
			},
		)
		bundle = add_serial_batch_ledgers(
			json.dumps(
				[{"batch_no": batch, "qty": qty} for batch, qty in zip(self.batches, quantities, strict=True)]
			),
			json.dumps(row.as_dict()),
			json.dumps(reconciliation.as_dict()),
			self.warehouse,
		)
		row.serial_and_batch_bundle = bundle.name
		return reconciliation

	def assert_batch_balances(self, batches, quantities):
		for batch, qty in zip(batches, quantities, strict=True):
			with self.subTest(batch=batch):
				self.assertEqual(
					get_batch_qty(
						batch,
						self.warehouse,
						for_stock_levels=True,
						consider_negative_batches=True,
						ignore_reserved_stock=True,
					),
					qty,
				)
