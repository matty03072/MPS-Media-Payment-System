"""Small, bounded API adapters. Never include upstream bodies in errors/logs."""
from urllib.parse import urlsplit, quote
import requests
import uuid


class APIError(Exception):
    pass


def request(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=(5, 30), allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise APIError(f"Remote service returned HTTP {response.status_code}. Check credentials or service availability.")
        return response.json() if response.content else {}
    except (requests.RequestException, ValueError):
        raise APIError("Remote service could not be reached or returned an invalid response.") from None


class Connect:
    base = "https://connect.emby.media/service"
    headers = {"X-Application": "StreamingHome/0.2.0"}

    def register(self, username, email, password):
        result = request("POST", self.base + "/register", headers={**self.headers, "X-CONNECT-TOKEN": "CONNECT-REGISTER"},
                         data={"userName": username, "email": email, "rawpw": password})
        if result.get("Status") != "SUCCESS":
            # Do not echo remote text, which can contain user input.
            raise APIError("Connect registration was not accepted. The name/email may already exist, or Emby may require verification. Administrator review may be required.")

    def authenticate(self, email, password):
        result = request("POST", self.base + "/user/authenticate", headers=self.headers,
                         json={"nameOrEmail": email, "rawpw": password})
        if not ((result.get('ConnectAccessToken') and result.get('ConnectUserId')) or (result.get('AccessToken') and result.get('User'))):
            raise APIError("Emby Connect sign-in was not confirmed. Check your credentials and any verification email.")


class Emby:
    def __init__(self, url, key):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise APIError("Use an HTTP(S) server URL without credentials, query or fragment.")
        self.base = url.rstrip("/")
        self.headers = {"X-Emby-Token": key}

    def call(self, method, path, **kwargs):
        return request(method, self.base + path, headers=self.headers, **kwargs)

    def libraries(self):
        result = self.call("GET", "/Library/VirtualFolders")
        if not isinstance(result, list):
            raise APIError("This address did not return an Emby library list.")
        return [{"id": str(x["ItemId"]), "name": x["Name"]} for x in result if x.get("ItemId")]

    def users(self):
        return self.call("GET", "/Users")

    def create(self, name):
        result = self.call("POST", "/Users/New", json={"Name": name})
        if not result.get("Id"):
            raise APIError("Account creation returned no ID; administrator review is required before retrying.")
        return str(result["Id"])

    def policy(self, uid, folders, disabled=False, live_tv=False):
        # Emby item IDs are not permission IDs. Resolve each selected item to its GUID.
        libraries=[] if disabled else self.call('GET','/Library/VirtualFolders')
        mapping={}
        for library in libraries:
            raw=library.get('Guid')
            if not raw:
                try:raw=uuid.UUID(str(library.get('ItemId'))).hex
                except (ValueError,AttributeError):continue
            try:guid=uuid.UUID(str(raw)).hex
            except (ValueError,AttributeError):continue
            mapping[str(library.get('ItemId'))]=guid;mapping[guid]=guid
        if not disabled and any(str(folder) not in mapping for folder in folders):
            raise APIError('A selected library has no valid permission ID. Refresh libraries and review access.')
        permission_ids=[] if disabled else sorted({mapping[str(folder)] for folder in folders})
        self.call("POST", f"/Users/{quote(uid, safe='')}/Policy", json={
            "IsAdministrator": False, "IsDisabled": disabled, "IsHidden": True,
            "IsHiddenRemotely": True, "IsHiddenFromUnusedDevices": True,
            "EnableAllFolders": False, "EnabledFolders": permission_ids,
            "EnableAllDevices": True, "EnableAllChannels": bool(live_tv), "EnabledChannels": [],
            "EnableMediaPlayback": True, "EnableRemoteAccess": True,
            "EnableAudioPlaybackTranscoding": True, "EnableVideoPlaybackTranscoding": True,
            "EnablePlaybackRemuxing": True, "EnableUserPreferenceAccess": True,
            "EnableContentDeletion": False, "EnableContentDeletionFromFolders": [],
            "EnableContentDownloading": False, "EnablePublicSharing": False,
            "EnableRemoteControlOfOtherUsers": False, "EnableSharedDeviceControl": False,
            "EnableLiveTvAccess": bool(live_tv), "EnableLiveTvManagement": False,
            "EnableSubtitleManagement": False, "EnableSyncTranscoding": False,
            "EnableMediaConversion": False, "AllowCameraUpload": False,
            "AllowSharingPersonalItems": False})
        actual=self.call('GET',f'/Users/{quote(uid,safe="")}').get('Policy',{})
        if (actual.get('IsDisabled') != bool(disabled) or actual.get('IsAdministrator') is not False
                or actual.get('EnableAllFolders') is not False
                or set(actual.get('EnabledFolders') or []) != set(permission_ids)
                or actual.get('EnableLiveTvAccess') != bool(live_tv)):
            raise APIError('Emby did not confirm the requested access policy; automatic retry is pending.')


    def password(self, uid, password):
        self.call("POST", f"/Users/{quote(uid, safe='')}/Password", json={"NewPw": password, "ResetPassword": False})

    def link(self, uid, email):
        result = self.call("POST", f"/Users/{quote(uid, safe='')}/Connect/Link", params={"ConnectUsername": email})
        if not isinstance(result, dict) or not isinstance(result.get("IsPending"), bool):
            raise APIError("Connect linking returned an unexpected response. Administrator review is required.")
        return result["IsPending"]

    def unlink(self, uid):
        self.call('DELETE',f'/Users/{quote(uid,safe="")}/Connect/Link')

    def delete(self, uid):
        self.call("DELETE", f"/Users/{quote(uid, safe='')}")

    def rename(self, uid, name):
        user=self.call('GET',f'/Users/{quote(uid,safe="")}')
        if not isinstance(user,dict) or str(user.get('Id'))!=uid:
            raise APIError('Emby did not return the expected user.')
        user['Name']=name
        self.call('POST',f'/Users/{quote(uid,safe="")}',json=user)
