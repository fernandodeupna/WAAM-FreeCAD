L C:\Users\Public\Addilan\WAAM\user\Subprg\0_Inicio_Prog.nc
V.E.Capeado_Online=1	;Capeado Online activado
V.E.Material=1		;Inox
V.E.Modo_Hilo=2		;Doble hilo
V.E.BeadPauseTime=25	;tiempo de espera entre cordones
V.E.BeadPauseTemp=300	;temperatura a esperar entre cordones
V.E.PauseMode=1		;modo de espera entre cordones (0=a tiempo, 1= a temperatura)

G92 G90 X450 Y450 Z80

$WHILE V.E.ProgramaTerminado==0
"Control"
"Layer"
$ENDWHILE


M84
M30 