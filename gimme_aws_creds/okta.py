"""
Copyright 2016-present Nike, Inc.
Licensed under the Apache License, Version 2.0 (the "License");
You may not use this file except in compliance with the License.
You may obtain a copy of the License at
      http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and* limitations under the License.*
"""
import sys
import base64
import json
import xml.etree.ElementTree as ET
import requests

from bs4 import BeautifulSoup

class OktaClient(object):
    """
       The Okta Client Class performes the necessary API
       calls to Okta to get temporary AWS credentials. An
       Okta API key and URL must be provided.
    """
    def __init__(self, okta_api_key, idp_entry_url):
        self.okta_api_key = okta_api_key
        self.idp_entry_url = idp_entry_url

    def get_headers(self):
        """sets the default header"""
        headers = {'Accept' : 'application/json',
                   'Content-Type' : 'application/json',
                   'Authorization' : 'SSWS ' + self.okta_api_key}
        return headers


    def get_login_response(self, username, password):
        """ gets the login response from Okta and returns the json response"""
        headers = self.get_headers()
        response = requests.post(
            self.idp_entry_url + '/authn',
            json={'username': username, 'password': password},
            headers=headers
        )
        response_json = json.loads(response.text)
        if 'errorCode' in response_json:
            print("LOGIN ERROR: " + response_json['errorSummary'], "Error Code ", response_json['errorCode'])
            sys.exit(2)
        return response_json

    def get_app_links(self, login_resp):
        """ return appLinks obejct for the user """
        headers = self.get_headers()
        user_id = login_resp['_embedded']['user']['id']
        response = requests.get(self.idp_entry_url + '/users/' + user_id + '/appLinks',
                                headers=headers, verify=True)
        app_resp = json.loads(response.text)
        # create a list from appName = amazon_aws
        apps = []
        for app in app_resp:
            if app['appName'] == 'amazon_aws':
                apps.append(app)
        if 'errorCode' in app_resp:
            print("APP LINK ERROR: " + app_resp['errorSummary'], "Error Code ", app_resp['errorCode'])
            sys.exit(2)
        return apps

    def get_app(self, login_resp):
        """ gets a list of available apps and
        ask the user to select the app they want
        to assume a roles for and returns the selection
        """
        app_resp = self.get_app_links(login_resp)
        print("Pick an app:")
        # print out the apps and let the user select
        for i, app in enumerate(app_resp):
            print('[', i, ']', app["label"])
        selection = input("Selection: ")
        # make sure the choice is valid
        if int(selection) > len(app_resp):
            print("You selected an invalid selection")
            sys.exit(1)
        # delete
        return app_resp[int(selection)]["label"]

    def get_role(self, login_resp, aws_appname):
        """ gets a list of available roles and
        ask the user to select the role they want
        to assume and returns the selection
        """
        # get available roles for the AWS app
        headers = self.get_headers()
        user_id = login_resp['_embedded']['user']['id']
        response = requests.get(
            self.idp_entry_url + '/apps/?filter=user.id+eq+\"' +
            user_id + '\"&expand=user/' + user_id + '&limit=200',
            headers=headers, verify=True
        )
        role_resp = json.loads(response.text)
        # Check if this is a valid response
        if 'errorCode' in role_resp:
            print("ERROR: " + role_resp['errorSummary'], "Error Code ", role_resp['errorCode'])
            sys.exit(2)
        # print out roles for the app and let the uesr select
        for app in role_resp:
            if app['label'] == aws_appname:
                print("Pick a role:")
                roles = app['_embedded']['user']['profile']['samlRoles']
                for i, role in enumerate(roles):
                    print('[', i, ']:', role)
                selection = input("Selection: ")
                # make sure the choice is valid
                if int(selection) > len(roles):
                    print("You selected an invalid selection")
                    sys.exit(1)
                return roles[int(selection)]

    def get_app_url(self, login_resp, aws_appname):
        """ return the app link json for select aws app """
        app_resp = self.get_app_links(login_resp)
        for app in app_resp:
            if app['label'] == 'AWS_API':
                print(app['linkUrl'])
            if app['label'] == aws_appname:
                return app
        print("ERROR app not found:", aws_appname)
        sys.exit(2)

    def get_idp_arn(self, app_id):
        """ return the PrincipalArn based on the app instance id """
        headers = self.get_headers()
        response = requests.get(
            self.idp_entry_url + '/apps/' +
            app_id, headers=headers, verify=True)
        app_resp = json.loads(response.text)
        return app_resp['settings']['app']['identityProviderArn']

    def get_role_arn(self, link_url, token, aws_rolename):
        """ return the role arn for the selected role """
        headers = self.get_headers()
        saml_resp = requests.get(link_url + '/?onetimetoken=' + token, headers=headers, verify=True)
        saml_value = self.get_saml_assertion(saml_resp)
        print("SAML", saml_resp.text)
        # decode the saml so we can find our arns
        # https://aws.amazon.com/blogs/security/how-to-implement-federated-api-and-cli-access-using-saml-2-0-and-ad-fs/
        aws_roles = []
        root = ET.fromstring(base64.b64decode(saml_value))
        for saml2attribute in root.iter('{urn:oasis:names:tc:SAML:2.0:assertion}Attribute'):
            if saml2attribute.get('Name') == 'https://aws.amazon.com/SAML/Attributes/Role':
                for saml2attributevalue in saml2attribute.iter(
                        '{urn:oasis:names:tc:SAML:2.0:assertion}AttributeValue'):
                    aws_roles.append(saml2attributevalue.text)
        # grab the role ARNs that matches the role to assume
        for aws_role in aws_roles:
            chunks = aws_role.split(',')
            if aws_rolename in chunks[1]:
                return chunks[1]
        # if you got this far something went wrong
        print("ERROR no ARN found for", aws_rolename)
        sys.exit(2)

    @staticmethod
    def get_saml_assertion(response):
        """return the base64 SAML value object from the SAML Response"""
        saml_soup = BeautifulSoup(response.text, "html.parser")
        for inputtag in saml_soup.find_all('input'):
            if inputtag.get('name') == 'SAMLResponse':
                return inputtag.get('value')
