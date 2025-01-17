from models.models import Source, AppConfig, Document, DocumentMetadata, DocumentChunk, DataConnector
from typing import List, Optional
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from appstatestore.statestore import StateStore
import os
import uuid
import json
import importlib
from typing import Any

SCOPES = ['https://www.googleapis.com/auth/drive']
CLIENT_SECRETS = os.environ.get("GDRIVE_CLIENT_SECRETS")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL")

class GoogleDocsConnector(DataConnector):
    source_type: Source = Source.google_drive
    connector_id: int = 1
    config: AppConfig
    folder_name: str
    flow: Optional[Any] = None

    def __init__(self, config: AppConfig, folder_name: str):
        super().__init__(config=config, folder_name=folder_name)
                        

    async def authorize(self, redirect_uri: str, auth_code: Optional[str]) -> str | None:
        client_secrets = json.loads(CLIENT_SECRETS)
        flow = InstalledAppFlow.from_client_config(
            client_secrets,
            SCOPES, 
            redirect_uri=redirect_uri
        )

        if auth_code is not None:
            flow.fetch_token(code=auth_code)
            # Build the Google Drive API client with the credentials
            creds = flow.credentials
            creds_string = creds.to_json()
            StateStore().save_credentials(self.config, creds_string, self)
        else:
            # Generate the authorization URL
            auth_url, _ = flow.authorization_url(prompt='consent')
            return auth_url

    async def load(self, source_id: str) -> List[Document]:
        # initialize credentials
        credential_string = StateStore().load_credentials(self.config, self)
        credential_json = json.loads(credential_string)
        creds = Credentials.from_authorized_user_info(
            credential_json
        )

        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            creds_string = json.dumps(creds.to_json())
            StateStore().save_credentials(self.config, creds_string, self)
        service = build('drive', 'v3', credentials=creds)
        

        print("loading documents")
        folder_query = f"name='{self.folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folder_result = service.files().list(q=folder_query, fields="nextPageToken, files(id)").execute()
        folder_items = folder_result.get('files', [])
        print("folder items:", folder_items)

        if len(folder_items) == 0:
            print(f"No folder named '{self.folder_name}' was found.")
            raise Exception(f"No folder named '{self.folder_name}' was found.")
        elif len(folder_items) > 1:
            print(f"Multiple folders named '{self.folder_name}' were found. Using the first one.")

        folder_id = folder_items[0]['id']

        # List the files in the specified folder
        results = service.files().list(q=f"'{folder_id}' in parents and trashed = false",
                                    fields="nextPageToken, files(id, name, webViewLink)").execute()
        items = results.get('files', [])

        documents: List[Document] = []
        # Loop through each file and create documents
        for item in items:
            # Check if the file is a Google Doc
            # Retrieve the full metadata for the file
            file_metadata = service.files().get(fileId=item['id']).execute()
            mime_type = file_metadata.get('mimeType', '')
            if mime_type != 'application/vnd.google-apps.document':
                continue

            # Retrieve the document content
            doc = service.files().export(fileId=item['id'], mimeType='text/plain').execute()
            content = doc.decode('utf-8')

            documents.append(
                Document(
                    title=item['name'],
                    text=content,
                    url=item['webViewLink'],
                    source_type=Source.google_drive,
                    metadata=DocumentMetadata(
                        document_id=str(uuid.uuid4()),
                        source_id=source_id,
                        tenant_id=self.config.tenant_id
                    )
                )
            )
        return documents