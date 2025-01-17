import argparse
import base64
import json
import os
import re
import sys
from urllib.parse import urlparse
import requests

from pywidevine.pssh import PSSH
from pywidevine.device import Device
from pywidevine.cdm import Cdm


class ComplexJsonEncoder(json.JSONEncoder):
    def default(self, o):
        if hasattr(o, 'to_json'):
            return o.to_json()
        return json.JSONEncoder.default(self, o)


class LicenseChallenge():
    def __init__(self, otp: str, playback_info: str, href: str, tech: str, license_request: str):
        self.otp = otp
        self.playback_info = playback_info
        self.href = href
        self.tech = tech
        self.license_request = license_request

    def to_json(self):
        resp = {}

        if self.otp != '':
            resp['otp'] = self.otp
        if self.playback_info != '':
            resp['playbackInfo'] = self.playback_info
        if self.href != '':
            resp['href'] = self.href
        if self.tech != '':
            resp['tech'] = self.tech
        if self.license_request != '':
            resp['licenseRequest'] = self.license_request
        return resp


def get_video_id(token: str):
    playback_info = json.loads(base64.b64decode(token))['playbackInfo']
    return json.loads(base64.b64decode(playback_info))['videoId']


def get_video_reference(token: str):
    return json.loads(base64.b64decode(token))['href']


def get_mpd(video_id: str) -> str:
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36',
        'origin': 'https://dev.vdocipher.com/',
        'referer': 'https://dev.vdocipher.com/'
    }
    url = 'https://dev.vdocipher.com/api/meta/' + video_id
    req = requests.get(url, headers=headers)
    resp = req.json()
    return resp['dash']['manifest']


def get_pssh(mpd: str):
    req = requests.get(mpd)
    return re.search('<cenc:pssh>(.*)</cenc:pssh>', req.text).group(1)


def get_license_response(license_challenge: str, mpd: str, video_reference: str):
    origin_url = urlparse(mpd)
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36',
        'origin': f'{origin_url.scheme}://{origin_url.hostname}/',
        'referer': f'{origin_url.scheme}://{origin_url.hostname}/',
        'vdo-ref': video_reference
    }

    req = requests.post(
        'https://license.vdocipher.com/auth',
        json={'token': license_challenge},
        headers=headers
    )
    resp = req.json()
    return resp['license']


def setup_license_challenge(token: str, challenge: bytes):
    decoded_token = json.loads(base64.b64decode(token))
    otp = decoded_token['otp']
    playback_info = decoded_token['playbackInfo']
    href = decoded_token['href']
    tech = decoded_token['tech']
    challenge = base64.b64encode(challenge).decode('UTF-8')

    license_challenge = LicenseChallenge(
        otp,
        playback_info,
        href,
        tech,
        challenge
    )

    raw_license_resp = json.dumps(
        license_challenge.to_json(),
        cls=ComplexJsonEncoder
    )
    return base64.b64encode(raw_license_resp.encode('UTF-8')).decode('UTF-8')


def create_argument_parser():
    parser = argparse.ArgumentParser(description='Vdocipher downloader')

    parser.add_argument(
        '--wvd',
        help='The file path to the WVD file generated by pywidevine'
    )

    parser.add_argument(
        '--token',
        help='The auth token retrieved from the Vdocipher request'
    )

    args = parser.parse_args()

    if not args.wvd or not args.token:
        parser.print_help()
        sys.exit(1)
    return args


def main():
    parser = create_argument_parser()
    wvd = parser.wvd
    token = parser.token

    video_ref = get_video_reference(token)
    video_id = get_video_id(token)
    mpd = get_mpd(video_id)
    pssh = get_pssh(mpd)

    device = Device.load(wvd)
    cdm = Cdm.from_device(device)
    session_id = cdm.open()
    cdm.set_service_certificate(session_id, cdm.common_privacy_cert)
    challenge = cdm.get_license_challenge(
        session_id,
        PSSH(pssh),
        privacy_mode=True
    )

    license_challenge = setup_license_challenge(token, challenge)
    license_response = get_license_response(
        license_challenge,
        mpd,
        video_ref
    )

    cdm.parse_license(session_id, license_response)

    terminal_size = os.get_terminal_size().columns
    print('*' * terminal_size)

    for key in cdm.get_keys(session_id):
        if key.type == 'CONTENT':
            print(f'[{key.type}] {key.kid.hex}:{key.key.hex()}')

    print(f'[  MPD  ] {mpd}')
    print('*' * terminal_size)

    cdm.close(session_id)


if __name__ == '__main__':
    main()
