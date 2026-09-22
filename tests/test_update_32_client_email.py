"""Account creation email regressions; SMTP is mocked, no real mail is sent."""
import os
import smtplib
import unittest
from unittest.mock import patch
from models import User, db
import test_workflow_end_to_end as workflow


class ClientEmailTests(unittest.TestCase):
    setUp = workflow.InspectionWorkflowEndToEndTests.setUp
    tearDown = workflow.InspectionWorkflowEndToEndTests.tearDown
    _role = workflow.InspectionWorkflowEndToEndTests._role
    _post = workflow.InspectionWorkflowEndToEndTests._post

    def create_user(self):
        self._role('Admin', self.ids['admin'])
        return self._post('/api/users', data={'username':'New Client','email':'newclient@test.invalid',
            'status':'Active','roles':'Client User','modules':'TRANS','project_ids':str(self.ids['project'])})

    def test_create_user_generates_password_setup_email(self):
        with patch.dict(os.environ, {'GMAIL_ADDRESS':'sender@test.invalid','GMAIL_APP_PASSWORD':'test-only'}, clear=True), patch('mailer.smtplib.SMTP') as smtp:
            response = self.create_user()
            self.assertEqual(response.status_code,201,response.get_data(as_text=True))
            self.assertTrue(response.json['_email_sent'])
            self.assertNotIn('_setup_url',response.json)
            server=smtp.return_value.__enter__.return_value
            server.starttls.assert_called_once()
            server.login.assert_called_once_with('sender@test.invalid','test-only')
            args=server.sendmail.call_args.args
            self.assertEqual(args[1],['newclient@test.invalid'])
            from email import message_from_string
            message=message_from_string(args[2])
            text=message.get_payload()[0].get_payload(decode=True).decode()
            self.assertIn('one-time link',text)
            self.assertIn('24 hours',text)
            with self.app.app_context():
                self.assertIsNotNone(User.query.filter_by(email='newclient@test.invalid').one().reset_token)

    def test_missing_configuration_returns_fallback_without_smtp(self):
        with patch.dict(os.environ,{},clear=True),patch('mailer.smtplib.SMTP') as smtp:
            response=self.create_user()
            self.assertEqual(response.status_code,201)
            self.assertEqual(response.json['_email_status'],'not_configured')
            self.assertTrue(response.json['_setup_url'])
            smtp.assert_not_called()

    def test_delivery_failure_keeps_user_and_reports_failure(self):
        with patch.dict(os.environ,{'GMAIL_ADDRESS':'sender@test.invalid','GMAIL_APP_PASSWORD':'test-only'},clear=True),patch('mailer.smtplib.SMTP',side_effect=smtplib.SMTPException('test failure')):
            response=self.create_user()
            self.assertEqual(response.status_code,201)
            self.assertEqual(response.json['_email_status'],'failed')
            self.assertTrue(response.json['_setup_url'])
        with self.app.app_context():
            self.assertEqual(User.query.filter_by(email='newclient@test.invalid').count(),1)

    def test_email_configuration_status_is_admin_only_and_hides_credentials(self):
        self._role('Client User',self.ids['client'])
        self.assertEqual(self.http.get('/api/users/email-status').status_code,403)
        self._role('Admin',self.ids['admin'])
        with patch.dict(os.environ,{'SMTP_USERNAME':'sender@test.invalid','SMTP_PASSWORD':'secret-test'},clear=True):
            response=self.http.get('/api/users/email-status')
            self.assertTrue(response.json['configured'])
            self.assertNotIn('secret-test',response.get_data(as_text=True))
