* Provided neutral lumped model for the D67 component contract only.
* These are fixture parameters, not extracted physical-package properties.
.SUBCKT neutral_package die board ref
Rseries die neck 10
Lseries neck board 1n
Cload board ref 2p
.ENDS neutral_package
