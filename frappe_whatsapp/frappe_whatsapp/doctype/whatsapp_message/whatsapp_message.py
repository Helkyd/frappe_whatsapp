# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

#Last Modified: 04-03-2025

import json
import frappe
from frappe import _, throw
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request

from frappe_whatsapp.utils import get_whatsapp_account, format_number

### FROM ERPGULF
from frappe import _
from frappe.email.doctype.notification.notification import Notification, get_context, json
from frappe.core.doctype.role.role import get_info_based_on_role, get_user_info
import requests
import json
import io
import base64
from frappe.utils import now
import time
from frappe import enqueue
import os   #FIX 27-02-2025


class WhatsAppMessage(Document):
    def validate(self):
        self.set_whatsapp_account()

    def on_update(self):
        self.update_profile_name()

    def update_profile_name(self):
        number = self.get("from")
        if not number:
            return
        from_number = format_number(number)

        if (
            self.has_value_changed("profile_name")
            and self.profile_name
            and from_number
            and frappe.db.exists("WhatsApp Profiles", {"number": from_number})
        ):
            profile_id = frappe.get_value("WhatsApp Profiles", {"number": from_number}, "name")
            frappe.db.set_value("WhatsApp Profiles", profile_id, "profile_name", self.profile_name)

    def create_whatsapp_profile(self):
        number = format_number(self.get("from") or self.to)
        if not frappe.db.exists("WhatsApp Profiles", {"number": number}):
            frappe.get_doc({
                "doctype": "WhatsApp Profiles",
                "profile_name": self.profile_name,
                "number": number,
                "whatsapp_account": self.whatsapp_account
            }).insert(ignore_permissions=True)

    def set_whatsapp_account(self):
        """Set whatsapp account to default if missing"""
        if not self.whatsapp_account:
            account_type = 'outgoing' if self.type == 'Outgoing' else 'incoming'
            default_whatsapp_account = get_whatsapp_account(account_type=account_type)
            if not default_whatsapp_account:
                throw(_("Please set a default outgoing WhatsApp Account or Select available WhatsApp Account"))
            else:
                self.whatsapp_account = default_whatsapp_account.name

    """Send whats app messages."""
    def before_insert(self):
        """Send message."""
        self.set_whatsapp_account()
        # Route to template path when a template is selected,
        # since message_type is read_only and cannot be set from the UI.
        if self.template:
            self.message_type = "Template"
        if self.type == "Outgoing" and self.message_type != "Template":
            print ('ANEXXXXXXX')
            print (self.attach)
            if self.attach and not self.attach.startswith("http"):
                link = frappe.utils.get_url() + "/" + self.attach
            else:
                link = self.attach

            data = {
                "messaging_product": "whatsapp",
                "to": format_number(self.to),
                "type": self.content_type,
            }
            if self.is_reply and self.reply_to_message_id:
                data["context"] = {"message_id": self.reply_to_message_id}
            if self.content_type in ["document", "image", "video"]:
                data[self.content_type.lower()] = {
                    "link": link,
                    "caption": self.message,
                }
            elif self.content_type == "reaction":
                data["reaction"] = {
                    "message_id": self.reply_to_message_id,
                    "emoji": self.message,
                }
            elif self.content_type == "text":
                data["text"] = {"preview_url": True, "body": self.message}

            elif self.content_type == "audio":
                data["audio"] = {"link": link}

            elif self.content_type == "interactive":
                # Interactive message (buttons or list)
                data["type"] = "interactive"
                buttons_data = json.loads(self.buttons) if isinstance(self.buttons, str) else self.buttons

                if isinstance(buttons_data, list) and len(buttons_data) > 3:
                    # Use list message for more than 3 options (max 10)
                    data["interactive"] = {
                        "type": "list",
                        "body": {"text": self.message},
                        "action": {
                            "button": "Select Option",
                            "sections": [{
                                "title": "Options",
                                "rows": [
                                    {"id": btn["id"], "title": btn["title"], "description": btn.get("description", "")}
                                    for btn in buttons_data[:10]
                                ]
                            }]
                        }
                    }
                else:
                    # Use button message for 3 or fewer options
                    data["interactive"] = {
                        "type": "button",
                        "body": {"text": self.message},
                        "action": {
                            "buttons": [
                                {
                                    "type": "reply",
                                    "reply": {"id": btn["id"], "title": btn["title"]}
                                }
                                for btn in buttons_data[:3]
                            ]
                        }
                    }

            elif self.content_type == "flow":
                # WhatsApp Flow message
                if not self.flow:
                    frappe.throw(_("WhatsApp Flow is required for flow content type"))

                flow_doc = frappe.get_doc("WhatsApp Flow", self.flow)

                if not flow_doc.flow_id:
                    frappe.throw(_("Flow must be created on WhatsApp before sending"))

                # Determine flow mode - draft flows can be tested with mode: "draft"
                flow_mode = None
                if flow_doc.status != "Published":
                    flow_mode = "draft"
                    frappe.msgprint(_("Sending flow in draft mode (for testing only)"), indicator="orange")

                # Get first screen if not specified
                flow_screen = self.flow_screen
                if not flow_screen and flow_doc.screens:
                    flow_screen = flow_doc.screens[0].screen_id

                data["type"] = "interactive"
                data["interactive"] = {
                    "type": "flow",
                    "body": {"text": self.message or "Please fill out the form"},
                    "action": {
                        "name": "flow",
                        "parameters": {
                            "flow_message_version": "3",
                            "flow_id": flow_doc.flow_id,
                            "flow_cta": self.flow_cta or flow_doc.flow_cta or "Open",
                            "flow_action": "navigate",
                            "flow_action_payload": {
                                "screen": flow_screen
                            }
                        }
                    }
                }

                # Add draft mode for testing unpublished flows
                if flow_mode:
                    data["interactive"]["action"]["parameters"]["mode"] = flow_mode

                # Add flow token - generate one if not provided (required by WhatsApp)
                flow_token = self.flow_token or frappe.generate_hash(length=16)
                data["interactive"]["action"]["parameters"]["flow_token"] = flow_token

            try:
                print ('FAZ NOTIFY COM A DATA')
                print (data)
                self.notify(data)
                self.status = "Success"
            except Exception as e:
                self.status = "Failed"
                frappe.throw(f"Failed to send message {str(e)}")
        elif self.type == "Outgoing" and self.message_type == "Template" and not self.message_id:
            print ('BEFORE CALLING SEND TEMPLATE')
            self.send_template()

        self.create_whatsapp_profile()

    def send_template(self):
        """Send template."""
        print ('RUN SEND TEMPLATE...')
        template = frappe.get_doc("WhatsApp Templates", self.template)
        data = {
            "messaging_product": "whatsapp",
            "to": format_number(self.to),
            "type": "template",
            "template": {
                "name": template.actual_name or template.template_name,
                "language": {"code": template.language_code},
                "components": [],
            },
        }

        parameters = []
        template_parameters = []
        if template.sample_values:
            field_names = template.field_names.split(",") if template.field_names else template.sample_values.split(",")

            if self.body_param is not None:
                params = list(json.loads(self.body_param).values())
                for param in params:
                    parameters.append({"type": "text", "text": param})
                    template_parameters.append(param)
            elif self.flags.custom_ref_doc:
                custom_values = self.flags.custom_ref_doc
                for field_name in field_names:
                    value = custom_values.get(field_name.strip())
                    parameters.append({"type": "text", "text": value})
                    template_parameters.append(value)                    

            else:
                ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                for field_name in field_names:
                    value = ref_doc.get_formatted(field_name.strip())
                    parameters.append({"type": "text", "text": value})
                    template_parameters.append(value)

            self.template_parameters = json.dumps(template_parameters)

        # Always add the body component, even if parameters list is empty
        data["template"]["components"].append({
            "type": "body",
            "parameters": parameters,
        })

        if template.header_type:
            if self.attach:
                if template.header_type == 'IMAGE':

                    if self.attach.startswith("http"):
                        url = f'{self.attach}'
                    else:
                        url = f'{frappe.utils.get_url()}{self.attach}'
                    data['template']['components'].append({
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {
                                "link": url
                            }
                        }]
                    })

            elif template.sample:
                if template.header_type == 'IMAGE':
                    if template.sample.startswith("http"):
                        url = f'{template.sample}'
                    else:
                        url = f'{frappe.utils.get_url()}{template.sample}'
                    data['template']['components'].append({
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {
                                "link": url
                            }
                        }]
                    })

        # We check this before standard buttons because MPM is an interactive action
        has_mpm = False
        if self.product_catalog_json:
            try:
                catalog_data = json.loads(self.product_catalog_json)
                data['template']['components'].append({
                    "type": "button",
                    "sub_type": "mpm",
                    "index": "0",
                    "parameters": [
                        {
                            "type": "action",
                            "action": catalog_data
                        }
                    ]
                })
                has_mpm = True
            except Exception as e:
                frappe.log_error(f"Failed to parse Product Catalog JSON: {str(e)}", "WhatsApp MPM Error")

        if template.buttons:
            button_parameters = []
            for idx, btn in enumerate(template.buttons):
                # Shift index if MPM was added at index 0
                current_idx = str(idx + 1) if has_mpm else str(idx)

                if btn.button_type == "Quick Reply":
                    button_parameters.append({
                        "type": "button",
                        "sub_type": "quick_reply",
                        "index": current_idx,
                        "parameters": [{"type": "payload", "payload": btn.button_label}]
                    })
                elif btn.button_type == "Call Phone":
                    button_parameters.append({
                        "type": "button",
                        "sub_type": "phone_number",
                        "index": current_idx,
                        "parameters": [{"type": "text", "text": btn.phone_number}]
                    })
                elif btn.button_type == "Visit Website":
                    url = btn.website_url
                    if btn.url_type == "Dynamic":
                        ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                        url = ref_doc.get_formatted(btn.website_url)
                    button_parameters.append({
                        "type": "button",
                        "sub_type": "url",
                        "index": current_idx,
                        "parameters": [{"type": "text", "text": url}]
                    })

            if button_parameters:
                data['template']['components'].extend(button_parameters)

        #FIX 23-02-2025
        print ('WHATS MESSAGE DATAAAAAAAAAAAAAA')
        print (data)
        self.notify(data)

    def notify(self, data):
        """Notify."""
        whatsapp_account = frappe.get_doc(
            "WhatsApp Account",
            self.whatsapp_account,
        )
        token = whatsapp_account.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{whatsapp_account.url}/{whatsapp_account.version}/{whatsapp_account.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            self.message_id = response["messages"][0]["id"]

        except Exception as e:
            res = frappe.flags.integration_request.json().get("error", {})
            error_message = res.get("Error", res.get("message"))
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Notification Log",
                    "template": "Text Message",
                    "meta_data": frappe.flags.integration_request.json(),
                }
            ).insert(ignore_permissions=True)

            frappe.throw(msg=error_message, title=res.get("error_user_title", "Error"))

    def format_number(self, number):
        """Format number."""
        if number.startswith("+"):
            number = number[1 : len(number)]

        return number

    @frappe.whitelist()
    def send_read_receipt(self):
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": self.message_id
        }

        settings = frappe.get_doc(
            "WhatsApp Account",
            self.whatsapp_account,
        )

        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )

            if response.get("success"):
                self.status = "marked as read"
                self.save()
                return response.get("success")

        except Exception as e:
            res = frappe.flags.integration_request.json().get("error", {})
            error_message = res.get("Error", res.get("message"))
            frappe.log_error("WhatsApp API Error", f"{error_message}\n{res}")


    # to send whatsapp message and document using ultramsg
    #to create pdf
    def create_pdf(self,doc):
        print ('create PDF .....')
        print ('doctype ', doc.doctype)
        print (doc.name)
        print ('print format ')
        print (self.print_format)
        file = frappe.get_print(doc.doctype, doc.name, self.print_format, as_pdf=True)
        pdf_bytes = io.BytesIO(file)
        pdf_base64 = base64.b64encode(pdf_bytes.getvalue()).decode()
        in_memory_url = f"data:application/pdf;base64,{pdf_base64}"
        return in_memory_url

        
    # fetch pdf from the create_pdf function and send to whatsapp 
    @frappe.whitelist()
    def send_whatsapp_with_pdf(self,doc,context):
        memory_url=self.create_pdf(doc)
        token = frappe.get_doc('whatsapp message').get('token')
        msg1 = frappe.render_template(self.message, context)
        recipients = self.get_receiver_list(doc,context)
        
        multiple_numbers=[] 
        for receipt in recipients:
            number = receipt
            multiple_numbers.append(number)
        add_multiple_numbers_to_url=','.join(multiple_numbers)
        document_url= frappe.get_doc('whatsapp message').get('url')
        payload = {
            'token': token,
            'to':add_multiple_numbers_to_url,
            "filename": doc.name,
            "document": memory_url,
            "caption": msg1,
        }
        headers = {'content-type': 'application/x-www-form-urlencoded'} 
        try:
            time.sleep(10)
            response = requests.post(document_url, data=payload, headers=headers)
            if response.status_code == 200:
                response_json = response.json()
                if "sent" in  response_json and  response_json["sent"] == "true":
                    # Log success
                    current_time =now()# for geting current time
                    msg1 = frappe.render_template(self.message, context)
                    frappe.get_doc({"doctype":"ultramsg_4_ERPNext log","title":"Whatsapp message and pdf successfully sent ","message":msg1,"to_number":doc.custom_mobile_phone,"time":current_time }).insert()
                elif "error" in  response_json:
                    # Log error
                    frappe.log("WhatsApp API Error: " ,  response_json.get("error"))
                else:
                    # Log unexpected response
                    frappe.log("Unexpected response from WhatsApp API")
            else:
                # Log HTTP error
                frappe.log("WhatsApp API returned a non-200 status code: " ,str(response.status_code))
                return response
        except Exception as e:
            frappe.log_error(title='Failed to send notification', message=frappe.get_traceback())  

    
        
    #send message without pdf
    def send_whatsapp_without_pdf(self,doc,context):
        token = frappe.get_doc('whatsapp message').get('token')
        message_url =  frappe.get_doc('whatsapp message').get('message_url')
        msg1 = frappe.render_template(self.message, context)
        recipients = self.get_receiver_list(doc,context) 
        multiple_numbers=[] 
        for receipt in recipients:
            number = receipt
            multiple_numbers.append(number)
        add_multiple_numbers_to_url=','.join(multiple_numbers)
        payload = {
            'token': token,
            'to':add_multiple_numbers_to_url,
            'body':msg1,
        }
        headers = {'content-type': 'application/x-www-form-urlencoded'}
        try:
            time.sleep(10)
            response = requests.post(message_url, data=payload, headers=headers)
            # when the msg send is success then its details are stored into ultramsg_4_ERPNext log  
            if response.status_code == 200:
                response_json = response.json()
                if "sent" in  response_json and  response_json["sent"] == "true":
                    # Log success
                    current_time =now()# for geting current time
                    msg1 = frappe.render_template(self.message, context)
                    frappe.get_doc({"doctype":"ultramsg_4_ERPNext log","title":"Whatsapp message successfully sent ","message":msg1,"to_number":doc.custom_mobile_phone,"time":current_time }).insert()
                elif "error" in  response_json:
                    # Log error
                    frappe.log("WhatsApp API Error: " ,  response_json.get("error"))
                else:
                    # Log unexpected response
                    frappe.log("Unexpected response from WhatsApp API")
            else:
            # Log HTTP error
                frappe.log("WhatsApp API returned a non-200 status code: " ,str(response.status_code))
            return response.text
        except Exception as e:
            frappe.log_error(title='Failed to send notification', message=frappe.get_traceback())  
    

    # directly pass the function 
    # call the  send whatsapp with pdf function and send whatsapp without pdf function and it work with the help of condition 
    def send(self, doc):

        context = {"doc":doc, "alert": self, "comments": None}
        if doc.get("_comments"):
            context["comments"] = json.loads(doc.get("_comments"))
        if self.is_standard:
            self.load_standard_properties(context)      
        try:
            if self.channel == "whatsapp message":
                # if attach_print and print format both are working then it send pdf with message
                if self.attach_print or  self.print_format:
                    frappe.enqueue(
                        self.send_whatsapp_with_pdf(doc, context),
                        queue="short",
                        timeout=200,
                        doc=doc,
                        context=context
                        )
                        # otherwise send only message   
                else:
                    frappe.enqueue(
                    self.send_whatsapp_without_pdf(doc, context),
                    queue="short",
                    timeout=200,
                    doc=doc,
                    context=context
                    )
        except:
                frappe.log_error(title='Failed to send notification', message=frappe.get_traceback())  
        super(WhatsAppMessage, self).send(doc)
                
                        
    def get_receiver_list(self, doc, context):
        """return receiver list based on the doc field and role specified"""
        receiver_list = []
        for recipient in self.recipients:
                if recipient.condition:
                    if not frappe.safe_eval(recipient.condition, None, context):
                        continue
                if recipient.receiver_by_document_field:
                    fields = recipient.receiver_by_document_field.split(",")
                    if len(fields)>1:
                        for d in doc.get(fields[1]):
                            phone_number = d.get(fields[0])
                            receiver_list.append(phone_number)
                    
                # For sending messages to the owner's mobile phone number
                if recipient.receiver_by_document_field == "owner":
                        receiver_list += get_user_info([dict(user_name=doc.get("owner"))], "mobile_no")
                        
                # For sending messages to the number specified in the receiver field
                elif recipient.receiver_by_document_field:
                        receiver_list.append(doc.get(recipient.receiver_by_document_field))
                # For sending messages to specified role
                if recipient.receiver_by_role:
                    receiver_list += get_info_based_on_role(recipient.receiver_by_role, "mobile_no")
                # return receiver_list
        receiver_list = list(set(receiver_list))
        # removing none_object from the list
        final_receiver_list = [item for item in receiver_list if item is not None]
        return final_receiver_list

  

def on_doctype_update():
    frappe.db.add_index("WhatsApp Message", ["reference_doctype", "reference_name"])


@frappe.whitelist()
def send_template(to, reference_doctype, reference_name, template):
    try:
        doc = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": to,
            "type": "Outgoing",
            "message_type": "Template",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content_type": "text",
            "template": template
        })

        doc.save()
    except Exception as e:
        raise e


#AngolaERP Version of send with PDF
    
# fetch pdf from the create_pdf function and send to whatsapp 
@frappe.whitelist()
def send_whatsapp_with_pdf_v1(doc,recipients):
	import json
	print ('send_whatsapp_with_pdf_v1')

	print (type(doc))
	print ('doc ', doc)
	#print (doc.doctype)
	doc1 = json.loads(doc)
	print (doc1['doc_agt'])
    
	#memory_url= whazapp.create_pdf(doc=doc)
	whazapp = WhatsAppMessage(doc1['doctype'],doc1['name'])
	print ('passsssss')
	from types import SimpleNamespace
	obj = SimpleNamespace(**doc1)
	#memory_url= whazapp.create_pdf(doc=obj)
	#print ('memmory url ', memory_url)
	print ('OBJSSSS ', obj.doc_agt)
	#nome_docagt = frappe.get_site_path('public','files') + "/" + doc['doc_agt'].replace(' ','/').replace('/','-') + ".pdf" # doc1['doc_agt']
	nome_docagt = frappe.get_site_path('public','files') + "/" + obj.doc_agt.replace(' ','/').replace('/','-') + ".pdf" # doc1['doc_agt']
	print (nome_docagt)
	if os.path.isfile(nome_docagt):
		print ('ficheiro exist.........')
        

    #token = frappe.get_doc('whatsapp message').get('token')
    #msg1 = frappe.render_template(self.message, context)
	if not recipients:
		recipients = whazapp.get_receiver_list(doc=obj,context="TESTE WHATSAPP")
    
	multiple_numbers=[] 
	for receipt in recipients:
		number = receipt
		multiple_numbers.append(number)
	add_multiple_numbers_to_url=','.join(multiple_numbers)
	#document_url= frappe.get_doc('whatsapp message').get('url')
	if nome_docagt and not nome_docagt.startswith("http"):
		#Remove port from url
		link = frappe.utils.get_url().replace(":443","") + '/files' + nome_docagt[nome_docagt.rfind('/'):]
	else:
		link = nome_docagt
    #FIX 04-03-2025; Check if SI or Quotation
	payload = {
        'messaging_product': 'whatsapp', 
        'to': recipients, 
        'type': 'document', 
        'document': {
            'link': link, #'https://tl.angolaerp.co.ao/files/PP-25-2337bb43.pdf', 
            'caption': 'Factura de Venda: {0}'.format(doc1['doc_agt']) if doc1['doctype'] == "Sales Invoice" else 'Factura Proforma: {0}'.format(doc1['doc_agt'])
        }
    }    
	request_body = {
        "messaging_product": "whatsapp",
        "to": whazapp.format_number(recipients),
		"message": {
			"attachment": {
				"type": "template",
				"payload": {
						"template_type": "generic",
						"elements": [
							{
								"title": "Welcome!",
								"image_url": "https://raw.githubusercontent.com/fbsamples/original-coast-clothing/main/public/styles/male-work.jpg",
								"subtitle": "We have the right hat for everyone.",
								"default_action": {
									"type": "web_url",
									"url": "https://www.originalcoastclothing.com/",
									"webview_height_ratio": "tall",
								},
								"buttons": [
									{
										"type": "web_url",
										"url": "https://www.originalcoastclothing.com/",
										"title": "View Website"
									}, {
										"type": "postback",
										"title": "Start Chatting",
										"payload": "DEVELOPER_DEFINED_PAYLOAD"
									}
								]
							},
							{
								"title": "Welcome!",
								"image_url": "https://raw.githubusercontent.com/fbsamples/original-coast-clothing/main/public/styles/male-work.jpg",
								"subtitle": "We have the right hat for everyone.",
								"default_action": {
									"type": "web_url",
									"url": "https://www.originalcoastclothing.com/",
									"webview_height_ratio": "tall",
								},
								"buttons": [
									{
										"type": "web_url",
										"url": "https://www.originalcoastclothing.com/",
										"title": "View Website"
									}, {
										"type": "postback",
										"title": "Start Chatting",
										"payload": "DEVELOPER_DEFINED_PAYLOAD"
									}
								]
							}
						]
				}
			}
		}
	}
    
	print ('REQUEST BODY ********************')
	print (request_body)
	print ('PAYLOADdddd')
	print (payload)


	whazapp.notify(data=payload)
