from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in isoft_angola_tax_compliance/__init__.py
from isoft_angola_tax_compliance import __version__ as version

setup(
	name="isoft_angola_tax_compliance",
	version=version,
	description="Angolan tax compliance - retencao na fonte (Imposto Industrial) and IVA cativo withholding",
	author="ISOFT",
	author_email="abbasschokor225@gmail.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
