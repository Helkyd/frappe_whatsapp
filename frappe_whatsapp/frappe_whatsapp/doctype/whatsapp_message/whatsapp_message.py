# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import json
import frappe
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request


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
    """Send whats app messages."""

    def before_insert(self):
        """Send message."""
        if self.type == "Outgoing" and self.message_type != "Template":
            if self.attach and not self.attach.startswith("http"):
                link = frappe.utils.get_url() + "/" + self.attach
            else:
                link = self.attach

            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(self.to),
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
                data["text"] = {"link": link}

            try:
                self.notify(data)
                self.status = "Success"
            except Exception as e:
                self.status = "Failed"
                frappe.throw(f"Failed to send message {str(e)}")
        elif self.type == "Outgoing" and self.message_type == "Template" and not self.message_id:
            self.send_template()

    def send_template(self):
        """Send template."""
        template = frappe.get_doc("WhatsApp Templates", self.template)
        data = {
            "messaging_product": "whatsapp",
            "to": self.format_number(self.to),
            "type": "template",
            "template": {
                "name": template.actual_name or template.template_name,
                "language": {"code": template.language_code},
                "components": [],
            },
        }

        if template.sample_values:
            field_names = template.field_names.split(",") if template.field_names else template.sample_values.split(",")
            parameters = []
            template_parameters = []

            ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
            for field_name in field_names:
                value = ref_doc.get_formatted(field_name.strip())

                parameters.append({"type": "text", "text": value})
                template_parameters.append(value)

            self.template_parameters = json.dumps(template_parameters)

            data["template"]["components"].append(
                {
                    "type": "body",
                    "parameters": parameters,
                }
            )

        if template.header_type and template.sample:
            field_names = template.sample.split(",")
            header_parameters = []
            template_header_parameters = []

            ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
            for field_name in field_names:
                value = ref_doc.get_formatted(field_name.strip())
                
                header_parameters.append({"type": "text", "text": value})
                template_header_parameters.append(value)

            self.template_header_parameters = json.dumps(template_header_parameters)

            data["template"]["components"].append({
                "type": "header",
                "parameters": header_parameters,
            })

        #FIX 23-02-2025
        print ('WHATS MESSAGE DATAAAAAAAAAAAAAA')
        print (data)
        self.notify(data)

    def notify(self, data):
        """Notify."""
        settings = frappe.get_doc(
            "WhatsApp Settings",
            "WhatsApp Settings",
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
            self.message_id = response["messages"][0]["id"]

        except Exception as e:
            res = frappe.flags.integration_request.json()["error"]
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
	payload = {
		'recipient': {'id': add_multiple_numbers_to_url},
		"filename": obj.name,
		"message": "ENVIO DO PDF....",
	}
	whazapp.notify(data=payload)
    
    