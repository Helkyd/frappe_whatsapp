// Copyright (c) 2022, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on('WhatsApp Message', {
	refresh: function(frm) {
		if (frm.doc.type == 'Incoming'){
			frm.add_custom_button(__("Reply"), function(){
				//FIX 27-02-2025; Added reply_to_message_id and is_reply
				frappe.new_doc("WhatsApp Message", {"to": frm.doc.from,"reply_to_message_id": frm.doc.message_id,"is_reply": true});

			});
		}
	}
});
